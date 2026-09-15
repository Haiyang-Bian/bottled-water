"""Fixture-only ACL preparation for the standing-policy native gate.

Not a production authorization adapter. Caller must own a new, non-concurrent
fixture; this cannot be pointed at existing user business directories.
"""

import os
from pathlib import Path

READ_EXECUTE = 0x1200A9
MODIFY = 0x1301BF  # DELETE on the object; deliberately excludes FILE_DELETE_CHILD.
SECURITY_WRITE = 0xC0000
CONTENT_WRITE = 0xD0156  # No SYNCHRONIZE/READ_CONTROL denial; read must still work.
ALL = 0x1F01FF


def tree(root):
    info = root.lstat()
    if info.st_file_attributes & 0x400 or (not root.is_dir() and info.st_nlink > 1):
        raise RuntimeError("Unsupported alias in owned standing-policy fixture")
    yield root
    if root.is_dir():
        for child in sorted(root.iterdir()):
            yield from tree(child)


def add_aces(path, sid, entries):
    """Preserve existing entries, order new explicit deny/allow before inheritance."""
    import win32security as sec

    sd = sec.GetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT, 4)
    old = sd.GetSecurityDescriptorDacl()
    if old is None:
        raise RuntimeError("NULL fixture ACL")
    previous = [old.GetAce(i) for i in range(old.GetAceCount())]
    if any(ace[0][0] not in (0, 1) for ace in previous):
        raise RuntimeError("Unsupported ACE in test fixture")
    if any(sec.ConvertSidToStringSid(ace[-1]) == sid for ace in previous):
        raise RuntimeError("Policy SID already present")
    principal = sec.ConvertStringSidToSid(sid)
    added = [((kind, flags), mask, principal) for kind, flags, mask in entries]
    explicit = [a for a in previous if not a[0][1] & 0x10] + added
    explicit.sort(key=lambda a: 0 if a[0][0] == 1 else 1)
    ordered = explicit + [a for a in previous if a[0][1] & 0x10]
    acl = sec.ACL()
    for ace in ordered:
        method = acl.AddAccessDeniedAceEx if ace[0][0] == 1 else acl.AddAccessAllowedAceEx
        method(4, ace[0][1], ace[1], ace[2])
    sec.SetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT, 4, None, None, acl, None)


def replace_owned_allow(path, sid, mask, flags=3):
    """Diagnostic repair: alter only this fixture's policy ACEs, no inheritance flag changes."""
    import win32security as sec

    acl = sec.GetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT, 4).GetSecurityDescriptorDacl()
    for index in reversed(range(acl.GetAceCount())):
        if sec.ConvertSidToStringSid(acl.GetAce(index)[-1]) == sid:
            acl.DeleteAce(index)
    if mask:
        acl.AddAccessAllowedAceEx(4, flags, mask, sec.ConvertStringSidToSid(sid))
    sec.SetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT, 4, None, None, acl, None)


def describe(path, sid):
    import win32security as sec

    sd = sec.GetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT, 4)
    acl = sd.GetSecurityDescriptorDacl()
    return {"control": sd.GetSecurityDescriptorControl(),
            "policy_aces": [{"kind": acl.GetAce(i)[0][0], "flags": acl.GetAce(i)[0][1],
                             "mask": acl.GetAce(i)[1]}
                            for i in range(acl.GetAceCount())
                            if sec.ConvertSidToStringSid(acl.GetAce(i)[-1]) == sid]}


def fixture_acl(path):
    """Serializable fixture evidence; never replay this as a saved DACL."""
    import win32security as sec

    sd = sec.GetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT, 4)
    acl = sd.GetSecurityDescriptorDacl()
    return {"protected": bool(sd.GetSecurityDescriptorControl()[0] & 0x1000),
            "aces": [[*acl.GetAce(i)[0], acl.GetAce(i)[1],
                      sec.ConvertSidToStringSid(acl.GetAce(i)[-1])]
                     for i in range(acl.GetAceCount())]}


def protect_fixture_boundary(path, sid, entries):
    """Experimental inheritance barrier, exclusively on newly owned probe fixtures.

    This freezes inheritance for ALL principals, not only our capability. It is
    intentionally not exposed as a production preparation or cleanup mechanism.
    The caller journals original control bits before calling and verifies cleanup.
    """
    import win32security as sec

    old = sec.GetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT, 4).GetSecurityDescriptorDacl()
    previous = [old.GetAce(i) for i in range(old.GetAceCount())]
    if any(ace[0][0] not in (0, 1) for ace in previous):
        raise RuntimeError("Unsupported fixture ACE")
    previous = [a for a in previous if sec.ConvertSidToStringSid(a[-1]) != sid]
    added = [((0, flags), mask, sec.ConvertStringSidToSid(sid)) for flags, mask in entries]
    ordered = [a for a in previous if not a[0][1] & 0x10] + added
    ordered.sort(key=lambda a: 0 if a[0][0] == 1 else 1)
    ordered += [a for a in previous if a[0][1] & 0x10]
    acl = sec.ACL()
    for ace in ordered:
        method = acl.AddAccessDeniedAceEx if ace[0][0] == 1 else acl.AddAccessAllowedAceEx
        method(4, ace[0][1], ace[1], ace[2])
    # pywin32 accepts SECURITY_INFORMATION through a signed C long.
    sec.SetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT, -0x7FFFFFFC,
                            None, None, acl, None)


def restore_fixture_inheritance(path, original):
    """Restore only the recorded control bit, from the CURRENT ACL, after SID cleanup.

    Probe fixtures have no competing ACL writers. Production would need identity,
    mutation ownership and conflict handling; this function makes no such claim.
    """
    import win32security as sec

    acl = sec.GetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT, 4).GetSecurityDescriptorDacl()
    if not original["protected"]:
        # SetNamedSecurityInfo converted inherited entries to explicit at the
        # barrier. Remove those specific copies, not the original explicit ACL.
        # This is safe ONLY in our new fixture with no other ACL writers.
        for kind, flags, mask, sid in original["aces"]:
            if not flags & 0x10:
                continue
            for index in range(acl.GetAceCount()):
                ace = acl.GetAce(index)
                if (ace[0] == (kind, flags & ~0x10) and ace[1] == mask
                        and sec.ConvertSidToStringSid(ace[-1]) == sid):
                    acl.DeleteAce(index)
                    break
    flag = -0x80000000 if original["protected"] else 0x20000000
    sec.SetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT, 4 | flag, None, None, acl, None)


def audit_allow_masks(descriptions, limits):
    """A DENY entry cannot serve as evidence that an excessive capability ALLOW is safe.

This is only an audit of the fixture's policy principal, not a complete Windows
AccessCheck replacement. Actual positive and negative operations remain required.
"""
    violations = {}
    for path, limit in limits.items():
        if path not in descriptions:
            violations[path] = "missing_acl_evidence"
            continue
        granted = 0
        for ace in descriptions[path]["policy_aces"]:
            if ace["kind"] == 0 and not ace["flags"] & 8:
                granted |= ace["mask"]
        if granted & ~limit:
            violations[path] = {"allowed_mask": granted, "maximum_mask": limit}
    return violations


def cleanup_tree(root, sids):
    """Remove only our unique principals; never restore a saved full DACL."""
    import win32security as sec

    changed = 0
    for path in tree(root):
        sd = sec.GetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT, 4)
        acl = sd.GetSecurityDescriptorDacl()
        if acl is None:
            raise RuntimeError("Unexpected NULL ACL during fixture cleanup")
        ours = [i for i in range(acl.GetAceCount())
                if sec.ConvertSidToStringSid(acl.GetAce(i)[-1]) in sids]
        if ours:
            for i in reversed(ours):
                acl.DeleteAce(i)
            sec.SetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT, 4, None, None, acl, None)
            changed += 1
    for path in tree(root):
        acl = sec.GetNamedSecurityInfo(str(path), sec.SE_FILE_OBJECT, 4).GetSecurityDescriptorDacl()
        if any(sec.ConvertSidToStringSid(acl.GetAce(i)[-1]) in sids
               for i in range(acl.GetAceCount())):
            raise RuntimeError("Fixture cleanup did not remove every owned ACE")
    return changed


def environment(scratch):
    system = Path(os.environ["SystemRoot"])
    return {"SystemRoot": str(system), "WINDIR": str(system), "SystemDrive": system.drive,
            "PATH": str(system / "System32"), "TEMP": str(scratch), "TMP": str(scratch),
            "USERPROFILE": str(scratch), "APPDATA": str(scratch), "LOCALAPPDATA": str(scratch)}


def evaluate(result, narrow=False, *, separated=False):
    positive = {"read_work", "read_own_scratch"}
    if not narrow:
        positive |= {"write_work", "read_archive", "create_work", "rename_work_file",
                     "delete_work_file"}
    negative = {"write_archive", "read_private", "write_private", "read_other_scratch",
                "write_dac_archive", "write_owner_archive", "write_dac_work", "write_owner_work",
                "write_dac_anchor", "write_owner_anchor", "delete_child_group", "create_archive",
                "delete_archive_file", "rename_archive", "rename_ancestor"}
    if narrow:
        negative |= {"write_work", "read_archive", "create_work", "rename_work_file",
                     "delete_work_file"}
    else:
        negative |= {"write_dac_new", "write_owner_new"}
    if separated:
        negative |= {"delete_work_root_access", "delete_child_work", "rename_work_root"}
        if narrow:
            negative.add("create_work_directory")
        else:
            positive |= {"create_work_directory", "create_nested_file", "read_nested_file",
                         "delete_nested_file", "delete_work_directory"}
    checks = {key: result.get(key, {}).get("allowed") is True for key in positive}
    checks["read_work"] &= result.get("read_work", {}).get("value") == "work"
    if not narrow:
        checks["read_archive"] &= result.get("read_archive", {}).get("value") == "archive"
        if separated:
            checks["read_nested_file"] &= result.get("read_nested_file", {}).get("value") == "nested"
    for key in negative:
        item = result.get(key, {})
        checks[key] = item.get("allowed") is False and (
            item.get("winerror") == 5 or item.get("errno") == 13
        )
    return checks
