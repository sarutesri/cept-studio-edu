"""DSS script export — turn a Case schema into a self-contained .dss file.

The exported script can be opened and run in:
- OpenDSS (command-line / API)
- OpenDSS-G (the EPRI graphical front-end)
- Any future engine that reads OpenDSS scripts

Usage::

    from cept.export import export_dss
    path = export_dss(case, "my_study")
    # or with a specific output directory:
    path = export_dss(case, "my_study", out_dir="exports/")
"""

from cept.export.dss_script import export_dss

__all__ = ["export_dss"]
