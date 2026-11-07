import sys

if '--self-test' in sys.argv:
    import ssl
    import tkinter as tk
    import paramiko
    import certifi
    import websocket
    import tempfile
    from nbt_aad_pc.ui import App, installer_script
    from nbt_aad_pc.releases import validate_elf
    from nbt_aad_pc.relay import hello_frames
    assert ssl.create_default_context(cafile=certifi.where()).cert_store_stats()['x509_ca'] > 0
    if getattr(sys, 'frozen', False):
        from pathlib import Path
        bundle_root = Path(sys.executable).parents[1] if sys.platform == 'darwin' else Path(sys._MEIPASS)
        assert Path(certifi.where()).resolve().is_relative_to(bundle_root.resolve())
    assert b'--source PATH' in installer_script().read_bytes()
    assert hello_frames()[2] == bytes.fromhex('020000000a7373685f656e61626c65')
    with tempfile.TemporaryDirectory(prefix='nbt-aad-self-test-') as directory:
        root = tk.Tk()
        root.withdraw()
        app = App(root, state=directory)
        root.update()
        assert [button.cget('text') for button in app.action_buttons] == [
            'Состояние', 'Установить / обновить', 'Удалить', 'Очистить ГУ', 'Перезагрузить ГУ']
        assert not hasattr(app, 'ssh_open_button')
        app.close()
else:
    from nbt_aad_pc.ui import main
    main()
