"""Own ACEs on host-selected tool copies and a fresh Run directory only."""

import os
from pathlib import Path


def tree(path):
    info = path.lstat()
    if info.st_file_attributes & 0x400 or (not path.is_dir() and info.st_nlink != 1):
        raise RuntimeError("Run dependency/private directory contains an unsupported alias")
    yield path
    if path.is_dir():
        for child in sorted(path.iterdir()):
            yield from tree(child)


class RunAcl:
    def __init__(self, sid, journal):
        self.sid, self.journal, self.grants = sid, journal, []

    def grant(self, root, mask):
        import win32security as sec

        root = Path(root)
        objects = list(tree(root))
        value = root.stat()
        record = {"root": str(root), "identity": [value.st_dev, value.st_ino],
                  "sid": self.sid, "state": "intent"}
        self.grants.append(record)
        self.journal(self.grants)
        for target in objects:
            acl = sec.GetNamedSecurityInfo(str(target), 1, 4).GetSecurityDescriptorDacl()
            if acl is None:
                raise RuntimeError("NULL DACL in Run dependencies")
            if any(sec.ConvertSidToStringSid(acl.GetAce(i)[-1]) == self.sid
                   for i in range(acl.GetAceCount())):
                continue  # Already inherited from our root, never remove others' entries.
            acl.AddAccessAllowedAceEx(4, 3 if target.is_dir() else 0, mask,
                                     sec.ConvertStringSidToSid(self.sid))
            sec.SetNamedSecurityInfo(str(target), 1, 4, None, None, acl, None)
            checked = sec.GetNamedSecurityInfo(str(target), 1, 4).GetSecurityDescriptorDacl()
            if not any(sec.ConvertSidToStringSid(checked.GetAce(i)[-1]) == self.sid
                       for i in range(checked.GetAceCount())):
                raise RuntimeError("Run dependency ACE was not applied")
        record["state"] = "applied"
        self.journal(self.grants)

    def close(self):
        import win32security as sec

        for record in self.grants:
            if record["state"] == "removed":
                continue
            root = Path(record["root"])
            value = os.stat(root, follow_symlinks=False)
            if [value.st_dev, value.st_ino] != record["identity"]:
                raise RuntimeError("Run grant root changed; cleanup requires repair")
            for target in tree(root):
                acl = sec.GetNamedSecurityInfo(str(target), 1, 4).GetSecurityDescriptorDacl()
                for i in reversed(range(acl.GetAceCount())):
                    if sec.ConvertSidToStringSid(acl.GetAce(i)[-1]) == self.sid:
                        acl.DeleteAce(i)
                sec.SetNamedSecurityInfo(str(target), 1, 4, None, None, acl, None)
                checked = sec.GetNamedSecurityInfo(str(target), 1, 4).GetSecurityDescriptorDacl()
                if any(sec.ConvertSidToStringSid(checked.GetAce(i)[-1]) == self.sid
                       for i in range(checked.GetAceCount())):
                    raise RuntimeError("Run ACE cleanup unconfirmed")
            record["state"] = "removed"
            self.journal(self.grants)
