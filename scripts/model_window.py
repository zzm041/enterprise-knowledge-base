"""The former shared property-ai execution window has been revoked.

The historical implementation is inert text in reports/revoked-shared-window/.
This entry point performs no filesystem, network, lock or process operations.
A new independent evaluation design needs user approval before inference.
"""


class Window:
    def run(self):
        raise RuntimeError("UPSTREAM_READ_ONLY_PLAN_REQUIRED")


if __name__ == "__main__":
    raise SystemExit("UPSTREAM_READ_ONLY_PLAN_REQUIRED: no inference executed")
