"""Local Voilà / Jupyter server config — no authentication for local use."""

c.ServerApp.token    = ''
c.ServerApp.password = ''
c.ServerApp.open_browser = False   # don't auto-open a browser tab on start

# Cull idle kernels quickly to reclaim memory from abandoned sessions
# (e.g. browser reload creates a new kernel; old one lingers otherwise).
c.MappingKernelManager.cull_idle_timeout  = 120   # kill after 2 min idle
c.MappingKernelManager.cull_interval      = 30    # check every 30 s
c.MappingKernelManager.cull_connected     = True  # cull even if a client is connected
