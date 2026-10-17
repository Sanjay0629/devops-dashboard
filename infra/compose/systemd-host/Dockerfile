# A host that runs systemd as PID 1, for the M2.4 smoke test (TS-02).
#
# **This is a test fixture, never a deployment artefact.** It exists because
# `node_exporter`'s systemd collector reads unit state over the system bus, and
# the project's ordinary `node-exporter` container has no bus and no systemd —
# so nothing in the normal stack can produce `node_systemd_*` series, and the
# only way to show that stopping a unit surfaces as FAILED is to give the
# exporter a real systemd to ask.
#
# The exporter binary is copied from the same pinned image the normal stack
# runs, so the series this fixture produces are the ones that image produces.
FROM prom/node-exporter:v1.8.2 AS exporter

FROM debian:bookworm-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends systemd dbus procps \
 && rm -rf /var/lib/apt/lists/* \
 # Units that only matter on a real machine and slow or break boot in a
 # container: udev, the console, logind, timers and the tty targets.
 && systemctl mask \
      systemd-udevd.service systemd-udevd-kernel.socket systemd-udevd-control.socket \
      systemd-logind.service getty.target console-getty.service \
      systemd-timesyncd.service apt-daily.timer apt-daily-upgrade.timer \
      dpkg-db-backup.timer e2scrub_all.timer fstrim.timer \
      systemd-tmpfiles-clean.timer

COPY --from=exporter /bin/node_exporter /usr/local/bin/node_exporter
COPY units/ /etc/systemd/system/
RUN systemctl enable node-exporter.service dcp-fixture-app.service \
      dcp-fixture-stoppable.service dcp-fixture-broken.service

# systemd treats SIGRTMIN+3 as "shut down cleanly"; SIGTERM would be ignored.
STOPSIGNAL SIGRTMIN+3
CMD ["/lib/systemd/systemd"]
