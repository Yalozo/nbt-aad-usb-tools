package ru.nbtaad.prototype;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.ClipboardManager;
import android.content.Intent;
import android.content.SharedPreferences;
import android.text.InputType;
import android.view.View;
import android.view.WindowManager;
import android.widget.AdapterView;
import android.widget.ArrayAdapter;
import android.widget.CheckBox;
import android.widget.EditText;
import android.widget.ProgressBar;
import android.widget.Spinner;
import android.net.ConnectivityManager;
import android.net.LinkAddress;
import android.net.LinkProperties;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.os.Bundle;
import android.provider.Settings;
import android.widget.Button;
import android.widget.LinearLayout;
import android.widget.ScrollView;
import android.widget.TextView;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.DatagramPacket;
import java.net.DatagramSocket;
import java.net.Inet4Address;
import java.net.InetAddress;
import java.net.InterfaceAddress;
import java.net.NetworkInterface;
import java.net.Socket;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.LinkedHashSet;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicBoolean;
import java.util.concurrent.atomic.AtomicReference;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.Callable;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.Set;

public class MainActivity extends Activity {
    private static final String ENET_PHONE_IP = "169.254.107.10";
    private static final String DEFAULT_SSH_HOST = "169.254.199.99";
    private static final String SECONDARY_SSH_HOST = "169.254.199.119";
    private static final String RECHECK_ETHERNET = "recheckEthernet";
    private static final String TEMPORARY_SSH_MIGRATED = "temporarySshMigrated";
    private static final int SAVE_LOG = 1;
    private static final AtomicBoolean RUNNING = new AtomicBoolean();
    private static final int LOG_LIMIT = 512 * 1024;

    private final List<View> controls = new ArrayList<>();
    private final List<ReleaseStore.Release> visible = new ArrayList<>();
    private List<ReleaseStore.Release> online = new ArrayList<>(), saved = new ArrayList<>();
    private final Object logLock = new Object();
    private TextView output, activityStatus;
    private ProgressBar progress;
    private Spinner releases;
    private EditText password;
    private CheckBox previews, reboot;
    private ReleaseStore store;
    private SharedPreferences preferences;
    private File logFile;
    private String selectedReleaseId = "";
    private volatile String sessionSshHost = "";
    private boolean recheckEthernet, refreshingChoices;

    private interface Job { void run() throws Exception; }

    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        recheckEthernet = state != null && state.getBoolean(RECHECK_ETHERNET);
        preferences = getSharedPreferences("installer", MODE_PRIVATE);
        if (!preferences.getBoolean(TEMPORARY_SSH_MIGRATED, false)) {
            // Older versions used the same preference for manual and discovered
            // addresses. Carry that value into this session when upgrading.
            sessionSshHost = preferences.getString("host", "");
            preferences.edit().remove("host").putBoolean(TEMPORARY_SSH_MIGRATED, true).apply();
        }
        selectedReleaseId = preferences.getString("release", "");
        store = new ReleaseStore(new File(getFilesDir(), "packages"));
        logFile = new File(getFilesDir(), "operation.log");

        ScrollView scroll = new ScrollView(this);
        scroll.setFillViewport(true);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (16 * getResources().getDisplayMetrics().density);
        layout.setPadding(pad, pad, pad, pad);
        scroll.addView(layout);

        TextView title = new TextView(this);
        title.setTextSize(23);
        title.setText("NBT-AAD Installer");
        layout.addView(title);
        TextView intro = new TextView(this);
        intro.setText("Подключите ENET через USB–Ethernet или подключите телефон и ГУ к одной локальной сети. Включите зажигание.");
        layout.addView(intro);
        progress = new ProgressBar(this, null, android.R.attr.progressBarStyleHorizontal);
        progress.setIndeterminate(true);
        progress.setVisibility(View.GONE);
        layout.addView(progress);
        activityStatus = new TextView(this);
        activityStatus.setMaxLines(2);
        activityStatus.setVisibility(View.GONE);
        layout.addView(activityStatus);
        addButton(layout, "Параметры SSH и диагностики", this::connectionSettings);
        password = new EditText(this);
        password.setHint("Пароль root SSH");
        password.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_VARIATION_PASSWORD);
        password.setText("ts&SK412");
        layout.addView(password);
        controls.add(password);

        previews = new CheckBox(this);
        previews.setText("Показывать бета-релизы");
        previews.setChecked(preferences.getBoolean("previews", false));
        layout.addView(previews);
        controls.add(previews);
        releases = new Spinner(this, Spinner.MODE_DIALOG);
        releases.setContentDescription("Релиз NBT-AAD");
        layout.addView(releases, new LinearLayout.LayoutParams(
            LinearLayout.LayoutParams.MATCH_PARENT, LinearLayout.LayoutParams.WRAP_CONTENT));
        controls.add(releases);
        releases.setOnItemSelectedListener(new AdapterView.OnItemSelectedListener() {
            @Override public void onItemSelected(AdapterView<?> parent, View view, int position, long id) {
                if (refreshingChoices || position != releases.getSelectedItemPosition()) return;
                String choice = position > 0 && position <= visible.size() ? visible.get(position - 1).id : "";
                selectedReleaseId = choice;
                preferences.edit().putString("release", choice).apply();
            }
            @Override public void onNothingSelected(AdapterView<?> parent) {}
        });
        previews.setOnCheckedChangeListener((view, enabled) -> {
            preferences.edit().putBoolean("previews", enabled).apply();
            updateChoices();
        });
        addButton(layout, "Проверить релизы", () -> startJob("Проверка релизов", false, () -> {
            List<ReleaseStore.Release> available = ReleaseStore.online(requireInternet());
            List<ReleaseStore.Release> cached = store.local(this::log);
            runOnUiThread(() -> {
                online = available;
                saved = cached;
                updateChoices();
            });
            log("Подходящих релизов GitHub: " + available.size());
        }));
        addButton(layout, "Скачать заранее", () -> {
            String choice = selectedReleaseId;
            startJob("Загрузка комплекта", false, () -> {
                ReleaseStore.Release release = resolveRelease(choice);
                log(release.label());
                store.load(release, internetNetwork(), this::log);
                refreshSaved();
            });
        });

        reboot = new CheckBox(this);
        reboot.setText("Перезагрузить ГУ после изменения файлов");
        reboot.setChecked(preferences.getBoolean("reboot", true));
        reboot.setOnCheckedChangeListener((view, enabled) -> preferences.edit().putBoolean("reboot", enabled).apply());
        controls.add(reboot);
        layout.addView(reboot);
        addButton(layout, "Установить / обновить", () -> requestAction(HeadUnitInstaller.Action.INSTALL));
        addButton(layout, "Состояние", () -> requestAction(HeadUnitInstaller.Action.STATUS));
        addButton(layout, "Редактировать nbt-aad.env", this::openEnvEditor);
        addButton(layout, "Удалить", () -> requestAction(HeadUnitInstaller.Action.UNINSTALL));
        addButton(layout, "Очистить ГУ", () -> requestAction(HeadUnitInstaller.Action.PURGE));
        addButton(layout, "Перезагрузить ГУ", () -> requestAction(HeadUnitInstaller.Action.RESTART));
        addButton(layout, "Сохранить журнал", this::saveLog);
        addButton(layout, "Поделиться журналом", this::shareLog);

        output = new TextView(this);
        output.setTextIsSelectable(true);
        output.setPadding(0, pad, 0, pad);
        layout.addView(output);
        setContentView(scroll);

        try { output.setText(SshReader.text(readLog())); }
        catch (IOException e) { output.setText("Не удалось прочитать журнал."); }
        startJob(null, false, this::refreshSaved);
    }

    private void addButton(LinearLayout layout, String title, Runnable action) {
        Button button = new Button(this);
        button.setText(title);
        button.setOnClickListener(view -> action.run());
        layout.addView(button);
        controls.add(button);
    }

    @Override protected void onResume() {
        super.onResume();
        if (recheckEthernet && !RUNNING.get()) {
            recheckEthernet = false;
            startCheck();
        }
    }

    @Override protected void onSaveInstanceState(Bundle state) {
        super.onSaveInstanceState(state);
        state.putBoolean(RECHECK_ETHERNET, recheckEthernet);
    }

    @Override public void onBackPressed() {
        if (RUNNING.get()) {
            android.widget.Toast.makeText(this, "Дождитесь завершения операции.", android.widget.Toast.LENGTH_SHORT).show();
            return;
        }
        super.onBackPressed();
    }

    private void startCheck() { startJob("Проверка соединения", false, this::check); }

    private void startJob(String title, boolean keepAwake, Job job) {
        if (!RUNNING.compareAndSet(false, true)) return;
        for (View control : controls) control.setEnabled(false);
        progress.setVisibility(View.VISIBLE);
        activityStatus.setText(title == null ? "Проверяю сохранённые комплекты…" : title);
        activityStatus.setVisibility(View.VISIBLE);
        if (keepAwake) getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
        if (title != null) output.setText("");
        new Thread(() -> {
            try {
                if (title != null) {
                    synchronized (logLock) {
                        try (FileOutputStream ignored = new FileOutputStream(logFile)) {}
                    }
                    log(title);
                }
                job.run();
            } catch (SshReader.UnsupportedHeadUnitException e) {
                log("Ошибка: " + e.getMessage());
                runOnUiThread(() -> {
                    if (!isFinishing() && !isDestroyed()) {
                        new AlertDialog.Builder(this).setTitle("Несовместимое ГУ").setMessage(e.getMessage())
                            .setPositiveButton("OK", null).show();
                    }
                });
            } catch (Exception e) {
                log("Ошибка: " + (e.getMessage() == null ? e.getClass().getSimpleName() : e.getMessage()));
            } finally {
                runOnUiThread(() -> {
                    RUNNING.set(false);
                    getWindow().clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON);
                    progress.setVisibility(View.GONE);
                    if (title == null) activityStatus.setVisibility(View.GONE);
                    for (View control : controls) control.setEnabled(true);
                    if (recheckEthernet && hasWindowFocus()) {
                        recheckEthernet = false;
                        startCheck();
                    }
                });
            }
        }, "nbt-aad-operation").start();
    }

    private void log(String line) {
        synchronized (logLock) {
            byte[] bytes = (line + "\n").getBytes(StandardCharsets.UTF_8);
            if (bytes.length <= LOG_LIMIT - logFile.length()) {
                try (FileOutputStream out = new FileOutputStream(logFile, true)) { out.write(bytes); }
                catch (IOException ignored) {}
            }
        }
        runOnUiThread(() -> {
            int lastLine = line.lastIndexOf('\n');
            activityStatus.setText(line.substring(lastLine + 1));
            if (output.length() < LOG_LIMIT) output.append(line + "\n");
        });
    }

    private byte[] readLog() throws IOException {
        synchronized (logLock) {
            if (!logFile.isFile()) return new byte[0];
            try (InputStream in = new FileInputStream(logFile)) {
                return ReleaseStore.readBounded(in, LOG_LIMIT);
            }
        }
    }

    private void saveLog() {
        Intent intent = new Intent(Intent.ACTION_CREATE_DOCUMENT);
        intent.addCategory(Intent.CATEGORY_OPENABLE);
        intent.setType("text/plain");
        intent.putExtra(Intent.EXTRA_TITLE, "NBT-AAD-Android.log");
        startActivityForResult(intent, SAVE_LOG);
    }

    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request, result, data);
        if (request != SAVE_LOG || result != RESULT_OK || data == null || data.getData() == null) return;
        try (OutputStream out = getContentResolver().openOutputStream(data.getData(), "wt")) {
            if (out == null) throw new IOException("Не удалось открыть файл.");
            out.write(readLog());
        } catch (IOException e) { log("Не удалось сохранить журнал. " + e.getMessage()); }
    }

    private void shareLog() {
        try {
            Intent intent = new Intent(Intent.ACTION_SEND);
            intent.setType("text/plain");
            intent.putExtra(Intent.EXTRA_SUBJECT, "Журнал NBT-AAD Android");
            intent.putExtra(Intent.EXTRA_TEXT, SshReader.text(readLog()));
            startActivity(Intent.createChooser(intent, "Поделиться журналом"));
        } catch (IOException e) { log("Не удалось прочитать журнал. " + e.getMessage()); }
    }

    private void refreshSaved() {
        List<ReleaseStore.Release> local = store.local(this::log);
        runOnUiThread(() -> {
            saved = local;
            updateChoices();
        });
    }

    private void updateChoices() {
        refreshingChoices = true;
        try {
            Map<String, ReleaseStore.Release> merged = new LinkedHashMap<>();
            for (ReleaseStore.Release release : online) {
                if (!merged.containsKey(release.id)) merged.put(release.id, release);
            }
            for (ReleaseStore.Release release : saved) {
                if (!merged.containsKey(release.id)) merged.put(release.id, release);
            }
            visible.clear();
            for (ReleaseStore.Release release : merged.values()) {
                if (previews.isChecked() || !release.preview || isSaved(release)) visible.add(release);
            }
            java.util.Collections.sort(visible, (a, b) -> b.date.compareTo(a.date));
            List<String> labels = new ArrayList<>();
            labels.add("Авто — последний стабильный релиз");
            int selected = 0;
            for (int i = 0; i < visible.size(); i++) {
                ReleaseStore.Release release = visible.get(i);
                String details = release.date.substring(0, 10);
                if (!release.title.equals(release.tag)) details = "[" + release.tag + "] · " + details;
                if (release.preview) details += " · бета";
                if (isSaved(release)) details += " · скачан";
                labels.add(release.title + "\n" + details);
                if (release.id.equals(selectedReleaseId)) selected = i + 1;
            }
            ArrayAdapter<String> adapter = new ArrayAdapter<>(this, R.layout.release_selected, labels);
            adapter.setDropDownViewResource(R.layout.release_choice);
            releases.setAdapter(adapter);
            releases.setSelection(selected, false);
        } finally {
            refreshingChoices = false;
        }
    }

    private boolean isSaved(ReleaseStore.Release candidate) {
        for (ReleaseStore.Release release : saved) if (release.id.equals(candidate.id)) return true;
        return false;
    }

    private ReleaseStore.Release resolveRelease(String id) throws Exception {
        if (!id.isEmpty()) {
            for (ReleaseStore.Release release : visible) if (id.equals(release.id)) return release;
            throw new IOException("Выбранный комплект отсутствует. Проверьте список релизов.");
        }
        Network internet = internetNetwork();
        List<ReleaseStore.Release> choices = online;
        if (internet != null && choices.isEmpty()) {
            try {
                choices = ReleaseStore.online(internet);
                List<ReleaseStore.Release> fetched = choices;
                runOnUiThread(() -> { online = fetched; updateChoices(); });
            } catch (Exception e) {
                log("Не удалось обновить релизы. " + e.getMessage());
            }
        }
        for (ReleaseStore.Release release : choices) {
            if (!release.preview && (internet != null || isSaved(release))) return release;
        }
        for (ReleaseStore.Release release : saved) if (!release.preview) return release;
        throw new IOException("Стабильный комплект не найден. Проверьте релизы или выберите скачанный комплект.");
    }

    private void requestAction(HeadUnitInstaller.Action action) {
        String secret = password.getText().toString();
        if (action != HeadUnitInstaller.Action.RESTART && secret.isEmpty()) {
            new AlertDialog.Builder(this).setMessage("Введите пароль root.").setPositiveButton("OK", null).show();
            return;
        }
        boolean restart = reboot.isChecked();
        String choice = selectedReleaseId;
        Runnable proceed = () -> startJob(actionTitle(action), true, () -> perform(action, secret, choice, restart));
        String prompt;
        switch (action) {
            case UNINSTALL: prompt = "Удалить NBT-AAD и оставить настройки и резервные копии на ГУ?"; break;
            case PURGE: prompt = "Убрать правила NBT-AAD из сетевого фильтра и удалить приложение, настройки, временные загрузки и резервные копии с ГУ? Отменить очистку после запуска нельзя."; break;
            case RESTART: prompt = "Перезагрузить головное устройство по диагностике сейчас?"; break;
            default: proceed.run(); return;
        }
        new AlertDialog.Builder(this).setTitle(actionTitle(action)).setMessage(prompt)
            .setPositiveButton(action == HeadUnitInstaller.Action.PURGE ? "Очистить" : "Подтвердить",
                (dialog, button) -> proceed.run())
            .setNegativeButton("Отмена", null).show();
    }

    private static String actionTitle(HeadUnitInstaller.Action action) {
        switch (action) {
            case FIRMWARE: return "Чтение версии ГУ";
            case STATUS: return "Состояние NBT-AAD";
            case INSTALL: return "Установка / обновление";
            case UNINSTALL: return "Удаление NBT-AAD";
            case PURGE: return "Полная очистка NBT-AAD";
            case RESTART: return "Перезагрузка ГУ";
            default: throw new IllegalArgumentException();
        }
    }

    private void openEnvEditor() {
        String secret = password.getText().toString();
        if (secret.isEmpty()) {
            new AlertDialog.Builder(this).setMessage("Введите пароль root.").setPositiveButton("OK", null).show();
            return;
        }
        startJob("Чтение " + HeadUnitEnv.PATH, false, () -> {
            try (SshReader session = connectToHeadUnit(secret)) {
                if (session == null) return;
                HeadUnitEnv.Snapshot original = HeadUnitEnv.read(session::run);
                String text = HeadUnitEnv.normalize(original.text());
                String hostKey = session.hostKey();
                runOnUiThread(() -> {
                    if (!isFinishing() && !isDestroyed()) showEnvEditor(original, text, hostKey, secret);
                });
            }
        });
    }

    private void showEnvEditor(HeadUnitEnv.Snapshot original, String text, String hostKey, String secret) {
        LinearLayout content = new LinearLayout(this);
        content.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (20 * getResources().getDisplayMetrics().density);
        content.setPadding(pad, 0, pad, 0);
        TextView help = new TextView(this);
        help.setText((original.exists ? "" : "Файл будет создан при сохранении.\n")
            + "Один параметр NBT_ на строку. Комментарии начинаются с #. Плагин читает до 15 параметров.");
        content.addView(help);
        EditText editor = new EditText(this);
        editor.setInputType(InputType.TYPE_CLASS_TEXT | InputType.TYPE_TEXT_FLAG_MULTI_LINE
            | InputType.TYPE_TEXT_FLAG_NO_SUGGESTIONS);
        editor.setTypeface(android.graphics.Typeface.MONOSPACE);
        editor.setGravity(android.view.Gravity.TOP | android.view.Gravity.START);
        editor.setMinLines(5);
        editor.setMaxLines(9);
        editor.setHint("NBT_BT_TRACE=1");
        editor.setText(text);
        content.addView(editor);
        Button presets = new Button(this);
        presets.setText("Отладочные параметры");
        presets.setOnClickListener(view -> showEnvPresets(editor));
        content.addView(presets);
        TextView status = new TextView(this);
        status.setVisibility(View.GONE);
        content.addView(status);
        ScrollView scroll = new ScrollView(this);
        scroll.addView(content);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle(HeadUnitEnv.PATH).setView(scroll)
            .setPositiveButton("Сохранить", null).setNegativeButton("Отмена", null).create();
        dialog.show();
        dialog.getWindow().setSoftInputMode(WindowManager.LayoutParams.SOFT_INPUT_STATE_ALWAYS_HIDDEN
            | WindowManager.LayoutParams.SOFT_INPUT_ADJUST_RESIZE);
        Button save = dialog.getButton(AlertDialog.BUTTON_POSITIVE);
        Button cancel = dialog.getButton(AlertDialog.BUTTON_NEGATIVE);
        save.setOnClickListener(view -> {
            if (RUNNING.get()) return;
            String draft = editor.getText().toString();
            if (text.equals(HeadUnitEnv.normalize(draft))) { dialog.dismiss(); return; }
            byte[] bytes;
            try { bytes = HeadUnitEnv.validate(original, draft); }
            catch (IOException e) { editor.setError(e.getMessage()); return; }
            status.setText("Сохраняю файл на ГУ…");
            status.setVisibility(View.VISIBLE);
            editor.setEnabled(false);
            presets.setEnabled(false);
            save.setEnabled(false);
            cancel.setEnabled(false);
            dialog.setCancelable(false);
            startJob("Сохранение " + HeadUnitEnv.PATH, true, () -> {
                boolean saved = false;
                String error = "Подключение отменено.";
                try (SshReader session = connectToHeadUnit(secret)) {
                    if (session == null) return;
                    if (!hostKey.equals(session.hostKey())) throw new IOException("Подключено другое ГУ. Откройте редактор заново.");
                    HeadUnitEnv.save(session::run, original, bytes, this::log);
                    saved = true;
                    if (original.exists) log("Предыдущий файл сохранён в " + HeadUnitEnv.BACKUP);
                    log("Файл сохранён и проверен. Для применения параметров демона перезапустите NBT-AAD в iDrive.");
                } catch (Exception e) {
                    error = e.getMessage() == null ? "Не удалось сохранить файл." : e.getMessage();
                    throw e;
                } finally {
                    boolean success = saved;
                    String message = error;
                    runOnUiThread(() -> {
                        if (success) dialog.dismiss();
                        else {
                            status.setText(message);
                            editor.setEnabled(true);
                            presets.setEnabled(true);
                            save.setEnabled(true);
                            cancel.setEnabled(true);
                            dialog.setCancelable(true);
                        }
                    });
                }
            });
        });
    }

    private void showEnvPresets(EditText editor) {
        String[] keys = {"NBT_BT_TRACE", "NBT_MOST_LOG", "NBT_RAM_LOG", "NBT_DIAG"};
        String[] labels = {"Трассировка Bluetooth\nNBT_BT_TRACE", "Журнал шины MOST\nNBT_MOST_LOG",
            "Журнал в памяти ГУ\nNBT_RAM_LOG", "Диагностика окон и слоёв\nNBT_DIAG"};
        new AlertDialog.Builder(this).setTitle("Отладочные параметры").setItems(labels, (dialog, index) -> {
            String key = keys[index];
            // NBT_DIAG checks for presence, so NBT_DIAG=0 would still enable it.
            boolean presenceFlag = key.equals("NBT_DIAG");
            String[] options = presenceFlag ? new String[]{"Включить (=1)", "Убрать из файла"}
                : new String[]{"Включить (=1)", "Выключить (=0)", "Убрать из файла"};
            new AlertDialog.Builder(this).setTitle(key).setItems(options, (choice, mode) -> {
                String value = mode == 0 ? "1" : presenceFlag || mode == 2 ? null : "0";
                String updated = HeadUnitEnv.setFlag(editor.getText().toString(), key, value);
                editor.setText(updated);
                editor.setSelection(updated.length());
            }).setNegativeButton("Отмена", null).show();
        }).setNegativeButton("Отмена", null).show();
    }

    private void perform(HeadUnitInstaller.Action action, String secret, String choice, boolean reboot) throws Exception {
        if (action == HeadUnitInstaller.Action.RESTART) {
            restartByDiagnostics();
            return;
        }
        Map<String, byte[]> payload = null;
        if (action == HeadUnitInstaller.Action.INSTALL) {
            ReleaseStore.Release release = resolveRelease(choice);
            log(release.label());
            payload = store.load(release, internetNetwork(), this::log);
            refreshSaved();
        }
        byte[] script = null;
        if (action != HeadUnitInstaller.Action.FIRMWARE) {
            try (InputStream in = getAssets().open("nbt-aad.sh")) {
                script = ReleaseStore.readBounded(in, 256 * 1024);
            }
        }
        try (SshReader session = connectToHeadUnit(secret)) {
            if (session == null) return;
            try { log("Версия прошивки: " + session.firmware()); }
            catch (Exception e) {
                if (action == HeadUnitInstaller.Action.FIRMWARE) throw e;
                log("Версия прошивки недоступна. " + e.getMessage());
            }
            HeadUnitInstaller installer = new HeadUnitInstaller(session::run, this::log);
            switch (action) {
                case FIRMWARE: break;
                case STATUS: installer.status(script, store); break;
                default: installer.change(action, script, payload);
            }
        }
        if (reboot && (action == HeadUnitInstaller.Action.INSTALL || action == HeadUnitInstaller.Action.UNINSTALL
            || action == HeadUnitInstaller.Action.PURGE)) {
            try { restartByDiagnostics(); }
            catch (Exception e) { log("Файлы обработаны. Перезагрузку подтвердить не удалось. " + e.getMessage()); }
        }
    }

    private void restartByDiagnostics() throws Exception {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        String manualGateway = preferences.getString("gateway", "");
        List<GatewaySearch> found = new ArrayList<>();
        if (!manualGateway.isEmpty()) {
            for (EnetConnection route : connectionRoutes(cm, manualGateway)) {
                if (route.acceptsPeer(InetAddress.getByName(manualGateway))) {
                    found.add(new GatewaySearch(route, java.util.Collections.singleton(manualGateway)));
                }
            }
        } else {
            found = discoverLocalGateways(cm);
            if (found.isEmpty()) {
                for (EnetConnection route : ethernetConnections(cm)) {
                    GatewaySearch result = gateways(route);
                    if (!result.addresses.isEmpty()) found.add(result);
                }
            }
        }
        GatewaySearch selected = GatewaySearch.select(found);
        if (selected == null) {
            throw new IOException("Диагностический шлюз BMW не найден. Проверьте подключение или задайте IP диагностики.");
        }
        String gateway = selected.addresses.iterator().next();
        if (!probeHeadUnit(selected.route, gateway)) throw new IOException("Не удалось подтвердить диагностическую связь с ГУ.");
        log("Отправляю ECUReset 11 01 для ГУ 0x63 через " + gateway + ":6801.");
        try (Socket socket = selected.route.connect(InetAddress.getByName(gateway), 6801, 3000)) {
            HeadUnitDiagnostics.restart(socket);
        }
        sessionSshHost = "";
        log("ГУ подтвердило ECUReset ответом 51 01. Дождитесь загрузки штатного меню.");
    }

    private void connectionSettings() {
        LinearLayout fields = new LinearLayout(this);
        fields.setOrientation(LinearLayout.VERTICAL);
        int pad = (int) (20 * getResources().getDisplayMetrics().density);
        fields.setPadding(pad, 0, pad, 0);
        EditText host = new EditText(this), port = new EditText(this), gateway = new EditText(this);
        host.setSingleLine(true);
        host.setHint("IP SSH · пусто для авто");
        host.setText(preferences.getString("host", ""));
        fields.addView(host);
        port.setHint("Порт SSH");
        port.setInputType(InputType.TYPE_CLASS_NUMBER);
        port.setText(Integer.toString(preferences.getInt("port", 22)));
        fields.addView(port);
        gateway.setSingleLine(true);
        gateway.setHint("IP диагностики · пусто для авто");
        gateway.setText(preferences.getString("gateway", ""));
        fields.addView(gateway);
        AlertDialog dialog = new AlertDialog.Builder(this).setTitle("Параметры SSH и диагностики")
            .setView(fields).setPositiveButton("Сохранить", null).setNegativeButton("Отмена", null).create();
        dialog.setOnShowListener(unused -> dialog.getButton(AlertDialog.BUTTON_POSITIVE).setOnClickListener(view -> {
            String hostValue = host.getText().toString().trim(), gatewayValue = gateway.getText().toString().trim();
            if (!hostValue.isEmpty() && !localIpv4(hostValue)) { host.setError("Введите локальный IPv4."); return; }
            if (!gatewayValue.isEmpty() && !localIpv4(gatewayValue)) { gateway.setError("Введите локальный IPv4."); return; }
            int number;
            try { number = Integer.parseInt(port.getText().toString()); }
            catch (NumberFormatException e) { number = 0; }
            if (number < 1 || number > 65535) { port.setError("Введите порт от 1 до 65535."); return; }
            if (number != 22 && hostValue.isEmpty()) { host.setError("Укажите IP для нестандартного порта."); return; }
            preferences.edit().putString("host", hostValue).putInt("port", number)
                .putString("gateway", gatewayValue).apply();
            sessionSshHost = "";
            dialog.dismiss();
        }));
        dialog.show();
    }

    private static boolean localIpv4(String text) {
        if (!text.matches("(?:[0-9]{1,3}\\.){3}[0-9]{1,3}")) return false;
        try {
            InetAddress ip = InetAddress.getByName(text);
            return ip instanceof Inet4Address && (ip.isLinkLocalAddress() || ip.isSiteLocalAddress());
        } catch (IOException e) {
            return false;
        }
    }

    private List<EnetConnection> ethernetConnections(ConnectivityManager cm) throws IOException {
        return localConnections(cm, false);
    }

    private List<EnetConnection> localConnections(ConnectivityManager cm, boolean allAddresses) throws IOException {
        List<EnetConnection> routes = new ArrayList<>();
        Set<String> managedInterfaces = new LinkedHashSet<>();
        for (Network network : cm.getAllNetworks()) {
            NetworkCapabilities caps = cm.getNetworkCapabilities(network);
            LinkProperties props = cm.getLinkProperties(network);
            if (caps == null || props == null) continue;
            boolean ethernet = caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET);
            boolean wifi = caps.hasTransport(NetworkCapabilities.TRANSPORT_WIFI);
            if (allAddresses || !ethernet) managedInterfaces.add(props.getInterfaceName());
            if (caps.hasTransport(NetworkCapabilities.TRANSPORT_VPN) || !(ethernet || (allAddresses && wifi))) {
                continue;
            }
            if (!allAddresses) log("Ethernet: " + props.getInterfaceName() + " " + props.getLinkAddresses());
            for (LinkAddress address : props.getLinkAddresses()) {
                InetAddress ip = address.getAddress();
                if (ip instanceof Inet4Address && !ip.isAnyLocalAddress() && !ip.isLoopbackAddress()) {
                    routes.add(new EnetConnection(network, props.getInterfaceName(),
                        (Inet4Address) ip, address.getPrefixLength(), wifi));
                    if (!allAddresses) break;
                }
            }
        }
        if (!allAddresses && !routes.isEmpty()) return routes;

        java.util.Enumeration<NetworkInterface> interfaces = NetworkInterface.getNetworkInterfaces();
        while (interfaces != null && interfaces.hasMoreElements()) {
            NetworkInterface iface = interfaces.nextElement();
            String name = iface.getName();
            if (!name.matches("(?i)(eth[0-9]*|en[0-9]+|enx[0-9a-f]+|usb[0-9]*|rndis[0-9]*)")
                || managedInterfaces.contains(name) || !iface.isUp() || iface.isLoopback()) continue;
            for (InterfaceAddress address : iface.getInterfaceAddresses()) {
                InetAddress ip = address.getAddress();
                int prefix = address.getNetworkPrefixLength();
                if (ip instanceof Inet4Address && (ip.isSiteLocalAddress() || ip.isLinkLocalAddress())
                    && prefix > 0 && prefix <= 32) {
                    routes.add(new EnetConnection(null, name, (Inet4Address) ip, prefix));
                }
            }
        }
        if (!allAddresses && routes.size() > 1) {
            throw new IOException("Найдено несколько адресов на проводных интерфейсах. Отключите лишние адаптеры и повторите проверку.");
        }
        if (!allAddresses) {
            for (EnetConnection route : routes) {
                log("Подключение через интерфейс " + route.interfaceName + " "
                    + route.address.getHostAddress() + "/" + route.prefixLength);
            }
        }
        return routes;
    }
    private List<EnetConnection> connectionRoutes(ConnectivityManager cm, String peer) throws IOException {
        List<EnetConnection> local = localConnections(cm, true);
        if (!peer.isEmpty()) {
            List<EnetConnection> matches = EnetConnection.routesForPeer(local, InetAddress.getByName(peer));
            if (!matches.isEmpty()) return matches;
        }
        List<EnetConnection> wired = ethernetConnections(cm);
        if (!wired.isEmpty() || peer.isEmpty()) return wired;
        List<EnetConnection> wifi = new ArrayList<>();
        for (EnetConnection route : local) {
            if (route.wifi && route.acceptsPeer(InetAddress.getByName(peer))) wifi.add(route);
        }
        return wifi;
    }

    private void showEthernetModemSetup() {
        runOnUiThread(() -> {
            if (isFinishing() || isDestroyed()) return;
            new AlertDialog.Builder(this)
                .setTitle("Ethernet без адреса IPv4")
                .setMessage("Подключите адаптер ENET и включите Ethernet-модем. На Samsung Galaxy откройте "
                    + "«Подключения» → «Мобильная точка доступа и модем» → «Ethernet-модем».\n\n"
                    + "После возврата приложение проверит связь. Если режим недоступен, задайте статический IP.")
                .setPositiveButton("Открыть настройки", (dialog, which) -> openSettings())
                .setNeutralButton("Статический IP", (dialog, which) -> showEthernetSetup())
                .setNegativeButton("Закрыть", null)
                .show();
        });
    }
    private void showEthernetSetup() {
        showEthernetSetup("");
    }
    private void showEthernetSetup(String explanation) {
        runOnUiThread(() -> {
            if (isFinishing() || isDestroyed()) return;
            new AlertDialog.Builder(this)
                .setTitle("Настройка Ethernet для ENET")
                .setMessage(explanation + "Подключите адаптер, выключите Ethernet-модем и выберите статический IP в настройках Ethernet.\n\n"
                    + "На Samsung Galaxy откройте «Подключения» → «Другие настройки соединения» → Ethernet.\n\n"
                    + "IP телефона — " + ENET_PHONE_IP + "\n"
                    + "Маска — 255.255.0.0 (длина префикса 16)\n"
                    + "Шлюз — 169.254.1.1, если поле обязательно\n\n"
                    + "Сохраните настройки и вернитесь в приложение. Оно снова проверит соединение.")
                .setPositiveButton("Скопировать IP и открыть", (dialog, which) -> openEthernetSettings())
                .setNegativeButton("Закрыть", null)
                .show();
        });
    }
    private void openEthernetSettings() {
        ClipboardManager clipboard = (ClipboardManager) getSystemService(CLIPBOARD_SERVICE);
        if (clipboard != null) clipboard.setPrimaryClip(ClipData.newPlainText("IP для ENET", ENET_PHONE_IP));
        openSettings();
    }
    private void openSettings() {
        recheckEthernet = true;
        try {
            startActivity(new Intent(Settings.ACTION_SETTINGS));
        } catch (ActivityNotFoundException e) {
            recheckEthernet = false;
            log("Откройте системные настройки вручную и найдите Ethernet.");
        }
    }
    private void check() throws Exception {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        String manualHost = preferences.getString("host", "");
        int port = preferences.getInt("port", 22);
        if (manualHost.isEmpty() && preferences.getString("gateway", "").isEmpty()) {
            List<GatewaySearch> found = discoverLocalGateways(cm);
            if (!found.isEmpty()) {
                for (GatewaySearch result : found) {
                    for (String gateway : result.addresses) probeHeadUnit(result.route, gateway);
                }
                List<SshEndpoint> hosts = existingLocalSsh(found, port);
                if (hosts.isEmpty()) log("Шлюз BMW найден. SSH не ответил на проверенных адресах.");
                for (SshEndpoint endpoint : hosts) {
                    log("SSH-сервер ГУ отвечает на " + endpoint.host + ":" + port + " через "
                        + endpoint.route.interfaceName + ".");
                }
                log("Проверка завершена. Ответ SSH означает доступность сервера, но не подтверждает вход.");
                return;
            }
        }
        List<EnetConnection> routes = connectionRoutes(cm,
            manualHost.isEmpty() ? preferences.getString("gateway", "") : manualHost);
        if (routes.isEmpty()) {
            log("Для связи с автомобилем нужен IPv4 на Ethernet.");
            showEthernetModemSetup();
            return;
        }
        for (EnetConnection candidate : routes) {
            GatewaySearch result = gateways(candidate);
            EnetConnection route = result.route;
            Set<String> gateways = result.addresses;
            boolean headUnitAnswered = false;
            if (!route.usesLocalAddress() || gateways.size() == 1) {
                for (String gateway : gateways) headUnitAnswered |= probeHeadUnit(route, gateway);
            }
            if (!manualHost.isEmpty()) {
                boolean sshAnswered = reportSsh(route, manualHost, port);
                if (!route.wifi && route.usesLocalAddress() && !headUnitAnswered && !sshAnswered) showEthernetSetup();
                continue;
            }
            if (!route.usesLocalAddress() && !route.wifi) {
                boolean sshAnswered = reportSsh(route, DEFAULT_SSH_HOST, 22);
                sshAnswered |= reportSsh(route, SECONDARY_SSH_HOST, 22);
                if (!sshAnswered) {
                    logSshSubnet(route, gateways.size() == 1 ? gateways.iterator().next() : null);
                    if (!headUnitAnswered && standardSshOutsideSubnet(route)) showEthernetSetup();
                }
                continue;
            }
            String gateway = gateways.size() == 1 ? gateways.iterator().next() : null;
            String host = findSsh(route, gateway, "", 22);
            if (host == null) host = scanSsh(route);
            if (host != null) log("SSH-сервер ГУ отвечает на " + host + ":22.");
            else {
                log("SSH-сервер ГУ не найден.");
                logSshSubnet(route, gateway);
            }
            if (!route.wifi && !headUnitAnswered && host == null) showEthernetSetup();
        }
        log("Проверка завершена. Ответ SSH означает доступность сервера, но не подтверждает вход.");
    }

    static final class GatewaySearch {
        final EnetConnection route;
        final Set<String> addresses;

        GatewaySearch(EnetConnection route, Set<String> addresses) {
            this.route = route;
            this.addresses = addresses;
        }

        static GatewaySearch select(List<GatewaySearch> found) throws IOException {
            if (found.isEmpty()) return null;
            if (found.size() != 1 || found.get(0).addresses.size() != 1) {
                throw new IOException("Найдено несколько шлюзов BMW. Укажите IP диагностики в параметрах.");
            }
            return found.get(0);
        }
    }

    private List<GatewaySearch> discoverLocalGateways(ConnectivityManager cm) throws IOException {
        List<GatewaySearch> matches = new ArrayList<>();
        Set<String> checked = new LinkedHashSet<>();
        for (EnetConnection route : localConnections(cm, true)) {
            if (route.prefixLength == 0 || route.broadcastAddress() == null) continue;
            String adapter = route.interfaceName + " (" + route.address.getHostAddress() + "/" + route.prefixLength + ")";
            if (!checked.add(adapter)) continue;
            log("Поиск шлюза BMW через " + adapter + "…");
            Set<String> addresses = new LinkedHashSet<>();
            EnetConnection fallback = route.localAddressFallback();
            if (!discoverUdp(route, addresses, true) && fallback != route) {
                route = fallback;
                discoverUdp(fallback, addresses, true);
            }
            for (String address : addresses) {
                log("Шлюз BMW " + address + " найден через " + adapter + ".");
            }
            if (!addresses.isEmpty()) matches.add(new GatewaySearch(route, addresses));
        }
        return matches;
    }

    private static final class SshEndpoint {
        final String host;
        final EnetConnection route;

        SshEndpoint(String host, EnetConnection route) {
            this.host = host;
            this.route = route;
        }
    }

    private List<SshEndpoint> existingLocalSsh(List<GatewaySearch> found, int port) throws Exception {
        List<SshEndpoint> hosts = new ArrayList<>();
        for (GatewaySearch result : found) {
            EnetConnection route = result.route;
            int previousCount = hosts.size();
            Set<String> candidates = new LinkedHashSet<>();
            candidates.add(DEFAULT_SSH_HOST);
            candidates.add(SECONDARY_SSH_HOST);
            candidates.addAll(result.addresses);
            for (String host : candidates) {
                InetAddress peer = InetAddress.getByName(host);
                if (!route.acceptsPeer(peer)) continue;
                try (Socket socket = route.connect(peer, port, 1200)) {
                    if (isQnxBanner(readSshBanner(socket, 1500))) {
                        hosts.add(new SshEndpoint(host, route));
                        break;
                    }
                } catch (IOException ignored) {}
            }
            if (hosts.size() == previousCount && port == 22) {
                log("Ищу SSH ГУ через " + route.interfaceName + " в локальной подсети.");
                for (String host : scan(route, 22)) hosts.add(new SshEndpoint(host, route));
            }
        }
        return hosts;
    }

    private GatewaySearch gateways(EnetConnection route) throws Exception {
        String manual = preferences.getString("gateway", "");
        if (manual.isEmpty()) return discover(route);
        if (!route.acceptsPeer(InetAddress.getByName(manual))) {
            log("Заданный адрес диагностики находится вне выбранной подсети Ethernet.");
            return new GatewaySearch(route, java.util.Collections.emptySet());
        }
        log("Адрес диагностики из настроек: " + manual);
        return new GatewaySearch(route, java.util.Collections.singleton(manual));
    }

    private GatewaySearch discover(EnetConnection route) throws Exception {
        Set<String> found = new LinkedHashSet<>();
        if (!discoverUdp(route, found)) {
            EnetConnection fallback = route.localAddressFallback();
            if (fallback != route) {
                route = fallback;
                log("Отправка через сеть Android не удалась. Повторяю поиск с привязкой к "
                    + route.interfaceName + " " + route.address.getHostAddress() + "/" + route.prefixLength + ".");
                discoverUdp(route, found);
            }
        }
        if (found.isEmpty() && route.usesLocalAddress()) {
            log("Шлюз не ответил на UDP. Проверяю порт 6801 в локальной подсети.");
            found.addAll(scan(route, 6801));
            if (found.size() == 1) {
                log("Кандидат на шлюз по TCP: " + found.iterator().next());
            } else if (found.size() > 1) {
                log("На порту 6801 ответили несколько адресов. Шлюз не определён.");
            }
        } else if (!found.isEmpty()) {
            log("Найдены шлюзы BMW: " + found);
        }
        if (found.isEmpty()) log("Шлюз BMW не найден.");
        return new GatewaySearch(route, found);
    }

    private boolean discoverUdp(EnetConnection route, Set<String> found) throws IOException {
        return discoverUdp(route, found, false);
    }

    private boolean discoverUdp(EnetConnection route, Set<String> found, boolean directedOnly) throws IOException {
        Set<InetAddress> destinations = new LinkedHashSet<>();
        InetAddress broadcast = route.broadcastAddress();
        if (broadcast != null) destinations.add(broadcast);
        if (!directedOnly && !route.usesLocalAddress()) {
            destinations.add(InetAddress.getByName("169.254.255.255"));
            destinations.add(InetAddress.getByName("255.255.255.255"));
        }
        boolean sent = false;
        if (!destinations.isEmpty()) {
            try (DatagramSocket socket = route.openDiscoverySocket(directedOnly)) {
                byte[] request = {0, 0, 0, 0, 0, 0x11};
                for (InetAddress destination : destinations) {
                    try {
                        socket.send(new DatagramPacket(request, request.length, destination, 6811));
                        sent = true;
                    } catch (IOException e) {
                        log("Ошибка отправки поиска BMW на " + destination.getHostAddress() + ": " + e.getMessage());
                    }
                }
                if (sent) readDiscoveryReplies(route, socket, found, directedOnly);
            } catch (IOException e) {
                log("Ошибка поиска шлюза: " + e.getMessage());
            }
        }
        return sent;
    }

    private void readDiscoveryReplies(EnetConnection route, DatagramSocket socket, Set<String> found, boolean directedOnly)
            throws IOException {
        long deadline = android.os.SystemClock.elapsedRealtime() + 3000;
        while (android.os.SystemClock.elapsedRealtime() < deadline) {
            socket.setSoTimeout((int) Math.max(1, deadline - android.os.SystemClock.elapsedRealtime()));
            byte[] data = new byte[2048];
            DatagramPacket packet = new DatagramPacket(data, data.length);
            try {
                socket.receive(packet);
            } catch (java.net.SocketTimeoutException e) {
                break;
            }
            if (packet.getLength() < 6 || data[4] != 0 || data[5] != 0x11
                || !route.acceptsPeer(packet.getAddress())
                || (directedOnly && !route.isInSubnet(packet.getAddress()))) continue;
            String body = new String(data, 6, packet.getLength() - 6, StandardCharsets.ISO_8859_1);
            if (body.contains("DIAGADR10") && body.contains("BMWVIN")) {
                found.add(packet.getAddress().getHostAddress());
            }
        }
    }

    private Set<String> scan(EnetConnection route, int port) throws Exception {
        List<Callable<String>> tasks = new ArrayList<>();
        for (InetAddress peer : route.scanPeers()) {
            tasks.add(() -> {
                try (Socket socket = route.connect(peer, port, 250)) {
                    if (port == 22 && !isQnxBanner(readSshBanner(socket, 650))) return null;
                    return peer.getHostAddress();
                } catch (IOException e) {
                    return null;
                }
            });
        }
        Set<String> found = new LinkedHashSet<>();
        ExecutorService pool = Executors.newFixedThreadPool(16);
        try {
            for (Future<String> task : pool.invokeAll(tasks)) {
                String peer = task.get();
                if (peer != null) found.add(peer);
            }
        } finally {
            pool.shutdownNow();
        }
        return found;
    }
    private boolean probeHeadUnit(EnetConnection route, String gateway) {
        try (Socket socket = route.connect(InetAddress.getByName(gateway), 6801, 3000)) {
            socket.setSoTimeout(3000);
            socket.getOutputStream().write(new byte[]{0, 0, 0, 5, 0, 1, (byte)0xf4, 0x63, 0x22, (byte)0xf1, (byte)0x90});
            java.io.DataInputStream in = new java.io.DataInputStream(socket.getInputStream());
            long deadline = android.os.SystemClock.elapsedRealtime() + 6000;
            while (android.os.SystemClock.elapsedRealtime() < deadline) {
                socket.setSoTimeout((int)Math.max(1, deadline - android.os.SystemClock.elapsedRealtime()));
                int length = in.readInt(); int control = in.readUnsignedShort();
                if (length < 0 || length > 65536) throw new java.io.IOException("Недопустимая длина ответа");
                byte[] data = new byte[length]; in.readFully(data);
                if (control == 0x43 || control == 0x45) break;
                if (control != 1 || length < 5 || (data[0] & 255) != 0x63 || (data[1] & 255) != 0xf4) continue;
                if ((data[2] & 255) == 0x62 && (data[3] & 255) == 0xf1 && (data[4] & 255) == 0x90) { log("ГУ 0x63 ответило на диагностический запрос."); return true; }
                if ((data[2] & 255) == 0x7f && data[3] == 0x22 && (data[4] & 255) != 0x78) {
                    if ((data[4] & 255) != 0x25) { log("ГУ 0x63 ответило отрицательным диагностическим кодом."); return true; }
                    break;
                }
            }
            log("Ответ ГУ 0x63 не получен.");
        } catch (Exception e) { log("Диагностика ГУ: ответ не получен (" + e.getClass().getSimpleName() + ")"); }
        return false;
    }
    private static String readSshBanner(Socket socket, int timeout) throws IOException {
        StringBuilder banner = new StringBuilder();
        long deadline = android.os.SystemClock.elapsedRealtime() + timeout;
        java.io.InputStream in = socket.getInputStream();
        for (int i = 0; i < 255; i++) {
            long remaining = deadline - android.os.SystemClock.elapsedRealtime();
            if (remaining <= 0) break;
            socket.setSoTimeout((int) remaining);
            int b = in.read();
            if (b == -1 || b == '\n') break;
            if (b >= 32 && b < 127) banner.append((char) b);
        }
        return banner.toString();
    }

    private static boolean isQnxBanner(String banner) {
        return banner.startsWith("SSH-") && banner.toUpperCase(Locale.ROOT).contains("QNX");
    }

    private boolean reportSsh(EnetConnection route, String host, int port) {
        try (Socket socket = route.connect(InetAddress.getByName(host), port, 2500)) {
            String banner = readSshBanner(socket, 2500);
            boolean valid = banner.startsWith("SSH-");
            log(host + ":" + port + " — " + (valid ? banner : "ответ не похож на приветствие SSH"));
            return valid;
        } catch (IOException e) {
            log(host + ":" + port + " — нет ответа (" + e.getClass().getSimpleName() + ")");
            return false;
        }
    }

    private void logSshSubnet(EnetConnection route, String gateway) throws IOException {
        if (!standardSshOutsideSubnet(route)) return;
        log("Адрес телефона " + route.address.getHostAddress() + "/" + route.prefixLength
            + ". Стандартные адреса SSH " + DEFAULT_SSH_HOST + " и " + SECONDARY_SSH_HOST
            + " вне этой подсети.");
        if (gateway != null) log("Шлюз " + gateway + " найден по диагностике. Адрес SSH ГУ может отличаться.");
    }

    private boolean standardSshOutsideSubnet(EnetConnection route) throws IOException {
        return !route.isInSubnet(InetAddress.getByName(DEFAULT_SSH_HOST))
            && !route.isInSubnet(InetAddress.getByName(SECONDARY_SSH_HOST));
    }

    private String findSsh(EnetConnection route, String gateway, String manualHost, int port) throws IOException {
        Set<String> hosts = new LinkedHashSet<>();
        if (manualHost.isEmpty()) {
            hosts.add(DEFAULT_SSH_HOST);
            hosts.add(SECONDARY_SSH_HOST);
            if (gateway != null) hosts.add(gateway);
        } else {
            hosts.add(manualHost);
        }
        for (String host : hosts) {
            InetAddress peer = InetAddress.getByName(host);
            if (!route.acceptsPeer(peer)) continue;
            try (Socket socket = route.connect(peer, port, 1200)) {
                String banner = readSshBanner(socket, 1500);
                if (isQnxBanner(banner) || (banner.startsWith("SSH-")
                    && (!manualHost.isEmpty() || (!route.usesLocalAddress() && host.equals(DEFAULT_SSH_HOST))))) {
                    return host;
                }
            } catch (IOException ignored) {}
        }
        return null;
    }

    private String scanSsh(EnetConnection route) throws Exception {
        log("Ищу SSH ГУ через " + route.interfaceName + " в локальной подсети.");
        List<SshEndpoint> hosts = new ArrayList<>();
        for (String host : scan(route, 22)) hosts.add(new SshEndpoint(host, route));
        SshEndpoint selected = selectSshEndpoint(hosts);
        return selected == null ? null : selected.host;
    }

    private SshEndpoint selectSshEndpoint(List<SshEndpoint> hosts) throws Exception {
        if (hosts.isEmpty()) return null;
        if (hosts.size() == 1) return hosts.get(0);
        String[] labels = new String[hosts.size()];
        for (int i = 0; i < hosts.size(); i++) {
            SshEndpoint endpoint = hosts.get(i);
            labels[i] = endpoint.host + " — " + endpoint.route.interfaceName;
            log("Найден SSH QNX: " + labels[i]);
        }
        CountDownLatch latch = new CountDownLatch(1);
        AtomicReference<SshEndpoint> choice = new AtomicReference<>();
        final AlertDialog[] dialog = new AlertDialog[1];
        runOnUiThread(() -> {
            if (isFinishing() || isDestroyed()) { latch.countDown(); return; }
            dialog[0] = new AlertDialog.Builder(this)
                .setTitle("Выберите IP SSH головного устройства")
                .setItems(labels, (d, index) -> {
                    SshEndpoint selected = hosts.get(index);
                    sessionSshHost = selected.host;
                    choice.set(selected);
                    latch.countDown();
                })
                .setNegativeButton("Отмена", (d, which) -> latch.countDown())
                .setOnCancelListener(d -> latch.countDown()).show();
        });
        if (!latch.await(2, TimeUnit.MINUTES)) {
            runOnUiThread(() -> { if (dialog[0] != null) dialog[0].dismiss(); });
        }
        if (choice.get() == null) throw new IOException("Выбор IP SSH отменён.");
        log("IP SSH выбран для текущего сеанса: " + choice.get().host);
        return choice.get();
    }

    private Network requireInternet() throws IOException {
        Network network = internetNetwork();
        if (network == null) throw new IOException("Подключите интернет через Wi-Fi или мобильную сеть.");
        return network;
    }

    private Network internetNetwork() {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        Network active = cm.getActiveNetwork();
        if (active != null && isInternet(cm.getNetworkCapabilities(active))) return active;
        for (Network network : cm.getAllNetworks()) {
            if (isInternet(cm.getNetworkCapabilities(network))) return network;
        }
        return null;
    }

    private static boolean isInternet(NetworkCapabilities caps) {
        return caps != null && !caps.hasTransport(NetworkCapabilities.TRANSPORT_ETHERNET)
            && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            && caps.hasCapability(NetworkCapabilities.NET_CAPABILITY_VALIDATED);
    }

    private SshReader connectToHeadUnit(String secret) throws Exception {
        ConnectivityManager cm = (ConnectivityManager) getSystemService(CONNECTIVITY_SERVICE);
        String manualHost = preferences.getString("host", "");
        int port = preferences.getInt("port", 22);
        if (manualHost.isEmpty() && !sessionSshHost.isEmpty()) {
            String sessionHost = sessionSshHost;
            for (EnetConnection route : connectionRoutes(cm, sessionHost)) {
                if (findSsh(route, null, sessionHost, port) != null) {
                    log("IP SSH текущего сеанса: " + sessionHost + ". Проверяю ключ и тип ГУ.");
                    return new SshReader(this, route, sessionHost, port, secret, this::confirmKey);
                }
            }
            sessionSshHost = "";
            log("IP SSH текущего сеанса недоступен. Повторяю поиск ГУ.");
        }
        GatewaySearch selected = null;
        if (manualHost.isEmpty() && preferences.getString("gateway", "").isEmpty()) {
            List<GatewaySearch> foundGateways = discoverLocalGateways(cm);
            List<SshEndpoint> hosts = existingLocalSsh(foundGateways, port);
            SshEndpoint found = selectSshEndpoint(hosts);
            if (found != null) {
                log("Подключение к " + found.host + ":" + port + ". Проверяю ключ и тип ГУ.");
                return new SshReader(this, found.route, found.host, port, secret, this::confirmKey);
            }
            selected = GatewaySearch.select(foundGateways);
        }
        List<EnetConnection> routes = selected == null
            ? connectionRoutes(cm, manualHost.isEmpty() ? preferences.getString("gateway", "") : manualHost)
            : java.util.Collections.singletonList(selected.route);
        if (routes.isEmpty()) {
            log("ГУ не найдено. Подключите телефон и ГУ к одной локальной сети или используйте ENET.");
            showEthernetModemSetup();
            return null;
        }
        if (routes.size() != 1) throw new IOException("Найдено несколько подходящих сетей. Оставьте одно подключение к ГУ.");
        EnetConnection route = routes.get(0);
        log("Подключение к ГУ через " + route.interfaceName + " (" + route.address.getHostAddress()
            + "/" + route.prefixLength + ").");
        if (!manualHost.isEmpty() && !route.acceptsPeer(InetAddress.getByName(manualHost))) {
            throw new IOException("Заданный IP SSH находится вне выбранной сети.");
        }
        String gateway = selected == null ? null : selected.addresses.iterator().next();
        String host = findSsh(route, gateway, manualHost, port);
        if (host == null) {
            if (port != 22) throw new IOException("SSH не отвечает на заданном порту.");
            GatewaySearch result = selected == null ? gateways(route) : selected;
            route = result.route;
            Set<String> gateways = result.addresses;
            if (gateways.size() > 1) throw new IOException("Найдено несколько шлюзов BMW. Укажите IP диагностики в параметрах.");
            if (gateways.size() == 1) gateway = gateways.iterator().next();
            if (!manualHost.isEmpty() && !route.acceptsPeer(InetAddress.getByName(manualHost))) {
                throw new IOException("Заданный IP SSH находится вне выбранной сети.");
            }
            if (gateway != null || route.usesLocalAddress()) host = findSsh(route, gateway, manualHost, port);
            if (host == null && manualHost.isEmpty() && selected == null
                && (route.usesLocalAddress() || gateway != null)) host = scanSsh(route);
        }
        if (host == null) {
            if (gateway == null || !probeHeadUnit(route, gateway)) {
                log("Не удалось подтвердить диагностическую связь с ГУ.");
                if (!route.wifi && (route.usesLocalAddress() || standardSshOutsideSubnet(route))) showEthernetSetup();
                return null;
            }
            if (manualHost.isEmpty()) logSshSubnet(route, gateway);
            Network internet = requireInternet();
            if (!confirm("Открытие SSH", "SSH не отвечает по найденным адресам. Запросить открытие через fix-bmw.ru? "
                + "Сервис получит диагностические данные, которые могут содержать VIN и сведения о ГУ.", "Открыть")) {
                log("Открытие SSH отменено.");
                return null;
            }
            log("Запрос открытия SSH через fix-bmw.ru…");
            RelayProtocol.UnknownCommandException relayError = null;
            try {
                UnlockRelay.activate(route, internet, gateway);
                log("Сервис подтвердил открытие SSH. Проверяю, доступен ли порт с телефона.");
            } catch (RelayProtocol.UnknownCommandException e) {
                relayError = e;
                log(e.getMessage() + ". Проверяю, открылся ли SSH ГУ.");
            }
            for (int attempt = 0; attempt < 3 && host == null; attempt++) {
                Thread.sleep(attempt == 0 ? 2500 : 1500);
                host = findSsh(route, gateway, manualHost, port);
            }
            if (host == null && manualHost.isEmpty()) host = scanSsh(route);
            if (host == null) {
                if (relayError != null) throw relayError;
                if (route.wifi) {
                    throw new IOException("Шлюз BMW найден через Wi-Fi, но SSH ГУ после открытия недоступен. "
                        + "Проверьте IP SSH в параметрах и подключение ГУ к локальной сети.");
                }
                if (!route.usesLocalAddress() && !standardSshOutsideSubnet(route)) {
                    throw new IOException("SSH не отвечает с телефона после открытия.");
                }
                log("SSH не отвечает с телефона после открытия. Переключитесь на статический IP или укажите известный IP SSH ГУ.");
                showEthernetSetup("Шлюз BMW ответил, но SSH ГУ по текущему маршруту недоступен. "
                    + "После переключения вернитесь в приложение и повторите нужное действие.\n\n");
                return null;
            }
        }
        log("Подключение к " + host + ":" + port + ". Проверяю ключ и тип ГУ.");
        return new SshReader(this, route, host, port, secret, this::confirmKey);
    }

    private boolean confirmKey(String host, String detail) throws Exception {
        return confirm("Ключ SSH: " + host, detail + "\n\nСверьте отпечаток с известным ключом ГУ.", "Доверять");
    }

    private boolean confirm(String title, String detail, String accept) throws InterruptedException {
        CountDownLatch latch = new CountDownLatch(1);
        AtomicBoolean answer = new AtomicBoolean();
        final AlertDialog[] dialog = new AlertDialog[1];
        runOnUiThread(() -> {
            if (isFinishing() || isDestroyed()) {
                latch.countDown();
                return;
            }
            dialog[0] = new AlertDialog.Builder(this).setTitle(title).setMessage(detail)
                .setPositiveButton(accept, (d, w) -> { answer.set(true); latch.countDown(); })
                .setNegativeButton("Отмена", (d, w) -> latch.countDown())
                .setOnCancelListener(d -> latch.countDown()).show();
        });
        if (latch.await(2, TimeUnit.MINUTES)) return answer.get();
        runOnUiThread(() -> { if (dialog[0] != null) dialog[0].dismiss(); });
        return false;
    }
}
