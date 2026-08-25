from pathlib import Path


class FileSystemGuard:
    PROTECTED_FILES = {
        ".env",
        ".env.local",
        ".env.production",
        "credentials.json",
        "secrets.json",
        "id_rsa",
        "id_ed25519",
    }
    WRITE_PROTECTED_FILES = {"agents.md"}

    def __init__(self, base_directory: Path) -> None:
        self.base_directory = base_directory.resolve()

    def resolve(
        self,
        path: str,
        allow_protected: bool = False,
        for_write: bool = False,
    ) -> Path:
        target = (self.base_directory / path).resolve()

        if not target.is_relative_to(self.base_directory):
            raise PermissionError(
                "Acesso fora do diretório permitido."
            )

        if (
            not allow_protected
            and (
                target.name.lower() in self.PROTECTED_FILES
                or (
                    for_write
                    and target.name.lower() in self.WRITE_PROTECTED_FILES
                )
            )
        ):
            raise PermissionError(
                "Este arquivo é protegido."
            )

        return target
