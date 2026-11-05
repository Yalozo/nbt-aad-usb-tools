import sys

if '--self-test' in sys.argv:
    import ssl
    import tkinter as tk
    import paramiko
    import certifi
    import tempfile
    from nbt_aad_pc.ui import App, installer_script
    from nbt_aad_pc.releases import validate_elf
    assert ssl.create_default_context(cafile=certifi.where()).cert_store_stats()['x509_ca'] > 0
    if getattr(sys, 'frozen', False):
        from pathlib import Path
        bundle_root = Path(sys.executable).parents[1] if sys.platform == 'darwin' else Path(sys._MEIPASS)
        assert Path(certifi.where()).resolve().is_relative_to(bundle_root.resolve())
    assert b'--source PATH' in installer_script().read_bytes()
    with tempfile.TemporaryDirectory(prefix='nbt-aad-self-test-') as directory:
        root = tk.Tk()
        root.withdraw()
        app = App(root, state=directory)
        root.update()
        assert [button.cget('text') for button in app.action_buttons] == [
            'Состояние', 'Установить / обновить', 'Удалить', 'Очистить ГУ', 'Перезагрузить ГУ']
        app.close()
else:
    from nbt_aad_pc.ui import main
    main()
