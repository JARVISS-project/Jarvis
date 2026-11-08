from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout
from hashlib import sha256

from jarvis.safe_browser.launcher import BrowserLauncher
from jarvis.safe_browser.models import PreparedBrowserLaunch
from jarvis.safe_browser.policy import UrlPolicy
from jarvis.safe_browser.registry import DESTINATIONS


class SafeBrowserService:
    def __init__(self, launcher: BrowserLauncher, policy: UrlPolicy | None = None,
                 *, timeout: float = 2.0) -> None:
        self.launcher = launcher
        self.policy = policy or UrlPolicy()
        self.timeout = timeout

    def available(self) -> bool:
        return self.launcher.association_token() is not None

    def status(self) -> str:
        return ("O Windows possui um navegador padrão utilizável."
                if self.available() else "Navegador padrão indisponível.")

    def destinations(self) -> str:
        labels = [item.label for key, item in DESTINATIONS.items()
                  if key != "browser_home"]
        return "Destinos disponíveis: " + ", ".join(labels) + "."

    def prepare_destination(self, destination_id: str) -> PreparedBrowserLaunch:
        token = self._association()
        request = self.policy.destination(destination_id, token)
        return PreparedBrowserLaunch(request, "open_destination")

    def prepare_search(self, provider: str, term: str) -> PreparedBrowserLaunch:
        token = self._association()
        normalized = self.policy.normalize_search_term(term)
        request = self.policy.search(provider, normalized, token)
        return PreparedBrowserLaunch(request, "web_search", provider, normalized)

    def revalidate(self, prepared: PreparedBrowserLaunch) -> None:
        self.policy.revalidate(prepared.request)
        current = self._association()
        if current != prepared.request.association_token:
            raise PermissionError("A associação do navegador mudou após o preparo.")

    def execute(self, prepared: PreparedBrowserLaunch) -> str:
        self.revalidate(prepared)
        executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="jarvis-browser-launch")
        future = executor.submit(self.launcher.launch, prepared.request)
        try:
            accepted = future.result(timeout=self.timeout)
        except FutureTimeout as error:
            future.cancel()
            raise TimeoutError("A solicitação ao navegador excedeu o timeout.") from error
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        if accepted is not True:
            raise RuntimeError("O Windows rejeitou a solicitação de abertura.")
        return "O Windows aceitou a solicitação de abertura."

    def search_target(self, prepared: PreparedBrowserLaunch) -> str:
        term_hash = sha256((prepared.term or "").encode("utf-8")).hexdigest()[:16]
        return f"web_search:{prepared.provider}:sha256:{term_hash}"

    def _association(self) -> str:
        token = self.launcher.association_token()
        if token is None:
            raise RuntimeError("Navegador padrão indisponível.")
        return token
