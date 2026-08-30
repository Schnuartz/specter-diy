p = 'src/platform.py'
s = open(p, encoding='utf-8').read()

old = '''            if unmounted or not self._present_or_gone():'''
new = '''            if unmounted or self._card_is_absent():'''
assert s.count(old) == 1
s = s.replace(old, new)

old = '''    def _present_or_gone(self):
        """
        is_present, but never raises. Used on cleanup paths, where a failing
        presence check must not mask the error we are already reporting; a
        card we cannot interrogate is treated as gone.
        """
        try:
            return self.is_present
        except Exception:
            return False'''
new = '''    def _card_is_absent(self):
        """
        True only when the card is POSITIVELY known to be gone.

        A presence probe that raises proves nothing - the bus may simply
        have glitched - so an unknown answer must count as "still there".
        Treating a failed probe as proof of absence would let a double fault
        (umount fails AND the probe fails) cut power while /sd is still
        mounted, which is exactly the inconsistent state the caller above
        exists to avoid. Erring the other way at worst leaves the interface
        powered until a retry succeeds.

        Never raises: a failing probe must not mask the error already being
        reported.
        """
        try:
            return not self.is_present
        except Exception as e:
            print(e)
            return False'''
assert s.count(old) == 1
s = s.replace(old, new)

# comment above the guard
old = '''            # Cut power only once the VFS mount is really gone - or once the
            # card is no longer there to retry against.'''
new = '''            # Cut power only once the VFS mount is really gone - or once the
            # card is positively known to be absent, so there is nothing left
            # to retry against.'''
assert s.count(old) == 1
s = s.replace(old, new)

open(p, 'w', encoding='utf-8', newline='\n').write(s)
import ast
ast.parse(s)
assert '_present_or_gone' not in s
print("PARSE_OK")
