import sys

if '--self-test' in sys.argv:
    import ssl
    import tkinter as tk
    import paramiko
    from nbt_aad_pc.ui import installer_script
    from nbt_aad_pc.releases import validate_elf
    assert ssl.create_default_context().cert_store_stats()['x509_ca'] > 0
    assert b'--source PATH' in installer_script().read_bytes()
    root = tk.Tk()
    root.withdraw()
    root.update()
    root.destroy()
else:
    from nbt_aad_pc.ui import main
    main()
