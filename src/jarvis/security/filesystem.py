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

    def __init__(self, base_directory: Path, permissions=None) -> None:
        self.base_directory = base_directory.resolve()
        self.permissions = permissions

    def require_operation(self, tool_name: str, target: Path, action: str) -> None:
        if self.permissions is not None:
            self.permissions.require_operation(tool_name, target, action)

    def resolve(
        self,
        path: str,
        allow_protected: bool = False,
        for_write: bool = False,
    ) -> Path:
        target = (self.base_directory / path).resolve()

        if self.permissions is not None:
            original = self.base_directory / path
            action = "write" if for_write else "read"
            if self.permissions.path_decision(original, action) == "block":
                raise PermissionError("Caminho bloqueado na Central de Permissões.")
            if self.permissions.has_path_restrictions():
                # Reject reparse traversal rather than claim atomic handle isolation.
                import stat
                for part in (original, *original.parents):
                    if part.exists() and (part.is_symlink() or getattr(part.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT):
                        raise PermissionError("Link/junction bloqueado no escopo protegido.")
                if target.is_file() and target.stat().st_nlink > 1:
                    raise PermissionError("Hard link bloqueado no escopo protegido.")
            protected_names = {"permissions.json", "settings.json", "skills.json", "jarvis.db"}
            if target.name.casefold() in protected_names or target.name.casefold().startswith("credentials") or target in self.permissions.protected_paths or any(p.is_dir() and target.is_relative_to(p) for p in self.permissions.protected_paths):
                raise PermissionError("Armazenamento administrativo protegido.")
            if target.is_file() and target.stat().st_nlink > 1:
                raise PermissionError("Hard link não oferece identidade administrativa segura.")

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
