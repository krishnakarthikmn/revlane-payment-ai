"""Sign-in: passwords, session tokens, and the people who may do what.

The first version signs people in with an email and a password. Single
sign-on and a second factor sit behind the two small interfaces in
`revlane.auth.providers`, stubbed until they are built.
"""

from revlane.auth.passwords import hash_password, verify_password
from revlane.auth.sessions import Principal, SessionError, issue, read

__all__ = ["Principal", "SessionError", "hash_password", "issue", "read", "verify_password"]
