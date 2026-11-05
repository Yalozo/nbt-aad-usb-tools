import sys

if '--self-test' in sys.argv:
    import ssl
    import tkinter as tk
    import paramiko
    import tempfile
    from nbt_aad_pc.ui import App, installer_script
    from nbt_aad_pc.releases import validate_elf
    assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0
    assert b'--source PATH' in installer_script().read_bytes()
    with tempfile.TemporaryDirectory(prefix='nbt-aad-self-test-') as directory:
        root = tk.Tk()
        root.withdraw()
        app = App(root, state=directory)
        root.update()
        assert app.removal.current() == 0
        app.close()
else:
    from nbt_aad_pc.ui import main
    main()
