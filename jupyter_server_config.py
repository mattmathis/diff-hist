"""Local Voilà / Jupyter server config — no authentication for local use."""

c.Voila.token    = ''
c.Voila.password = ''
c.Voila.ip       = '0.0.0.0'   # listen on all interfaces, not just localhost
c.Voila.open_browser = False   # don't auto-open a browser tab on start

# Cull idle kernels quickly to reclaim memory from abandoned sessions
# (e.g. browser reload creates a new kernel; old one lingers otherwise).
c.MappingKernelManager.cull_idle_timeout  = 120   # kill after 2 min idle
c.MappingKernelManager.cull_interval      = 30    # check every 30 s
c.MappingKernelManager.cull_connected     = True  # cull even if a client is connected
