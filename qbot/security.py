import hashlib
import hmac
import secrets


def password_hash(password: str) -> str:
    if len(password) < 12:
        raise ValueError("管理员密码至少需要 12 个字符")
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), 600000).hex()
    return f"pbkdf2_sha256$600000${salt}${digest}"


def valid_password_hash(value: str) -> bool:
    try:
        algorithm, rounds, salt, digest = value.split("$")
        return (algorithm == "pbkdf2_sha256" and int(rounds) == 600000
                and len(bytes.fromhex(salt)) == 16 and len(bytes.fromhex(digest)) == 32)
    except ValueError:
        return False


def verify_password(password: str, encoded: str) -> bool:
    if not valid_password_hash(encoded):
        return False
    _, rounds, salt, expected = encoded.split("$")
    actual = hashlib.pbkdf2_hmac("sha256", password.encode(), bytes.fromhex(salt), int(rounds)).hex()
    return hmac.compare_digest(actual, expected)
