# Platform Support

## Linux

The service installers are Linux installers. They generate **systemd user units** under:

`~/.config/systemd/user/`

Each service installer resolves the repository root from its own location, renders its
`*.service.template` file, reloads the user systemd manager, enables the unit, and
starts it.

A clean-checkout installer rendering test runs in CI without requiring a live systemd
user session. The Graph service uses the same Linux systemd user lifecycle and remains
loopback-only at `127.0.0.1:8766`.

## Native Windows

Native Windows service installation is **not supported by this installer set**.
These scripts require Bash and `systemctl --user`, and the generated units are
systemd units. They must not be run from native Windows shells or treated as Windows
service installers.

Native Windows service support is a separate platform concern and must use an
explicit Windows service implementation rather than pretending systemd is available.

## WSL

WSL distributions that provide a working Linux userspace and systemd user manager
may use the Linux installers when systemd user services are enabled. Native Windows
service management remains outside this repository's Linux installer path.


## Linux without systemd

The installers require `systemctl --user` and will not install units on a host
without a working systemd user manager. For explicit foreground/manual operation
on Linux, see [Service Lifecycle Ownership](SERVICE_LIFECYCLE.md#running-without-systemd).
The manual path is operator-managed only: no automatic daemon startup, restart
policy, or login integration is provided. Do not use these Linux installers from
native Windows shells.
