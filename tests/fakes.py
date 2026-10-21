"""Minimal in-memory stand-in for the Docker SDK so the engine can be tested without Docker."""
import types


class FakeContainer:
    def __init__(self, store, name, image, ports, env, labels, running=True):
        self._store, self.name, self.labels = store, name, labels
        self.status = "running" if running else "exited"
        self.attrs = {
            "Config": {"Image": image, "Env": ["PATH=/usr/bin"] + [f"{k}={v}" for k, v in (env or {}).items()]},
            "HostConfig": {"PortBindings": {p: [{"HostIp": "", "HostPort": str(h)}] for p, h in (ports or {}).items()}},
        }

    def start(self): self.status = "running"
    def stop(self, timeout=10): self.status = "exited"
    def remove(self, force=False): self._store.pop(self.name, None)


class FakeContainers:
    def __init__(self): self.store = {}

    def list(self, all=False, filters=None):
        label = (filters or {}).get("label")
        k, _, v = (label or "").partition("=")
        return [c for c in list(self.store.values()) if not label or c.labels.get(k) == v]

    def run(self, image, name, detach=True, ports=None, environment=None, labels=None):
        c = FakeContainer(self.store, name, image, ports, environment, labels or {})
        self.store[name] = c
        return c


class FakeImages:
    def get(self, ref): return types.SimpleNamespace(attrs={"Config": {"Env": ["PATH=/usr/bin"]}})
    def pull(self, ref): return None


class FakeClient:
    def __init__(self): self.containers, self.images = FakeContainers(), FakeImages()
