# Hosted workbench plugins

Source: Argus `e5d12046472603d43c4ff8f6893f5611619c28a8`.

The invitation runtimes declare CrystalPilot automatically and prepare its
workspace under each account's `ARGUS_SKILL_HOME/crystalpilot-runtime`.
The public portal permits the curated workbench's launch, conversations,
uploads and scientific operations, retaining invitation authentication,
same-origin checks and read-only restrictions. Installation, updates, removal
and arbitrary executable-path configuration remain service-owned; users can
check/repair the environment and submit their own SHELX download credentials.
Folder browsing and opening/importing a project cannot escape that account's
CrystalPilot workspace, including through symlinks. No backend credential or
plugin access cookie is forwarded to the browser.

The hosted workspace overlay hides host-managed setup controls. Generic plugin installation, verified preinstallation and offline PLATON provisioning remain in [Argus](https://github.com/lbx154/Argus/blob/chore/hosting-out/docs/workbench-plugins.md).
