"""Create (or promote) the first ADMIN. Admins can never be created via the website.

Run from the backend folder:
    python -m scripts.create_admin admin@example.com "Admin Name"
"""
import getpass
import sys

from pydantic import TypeAdapter, ValidationError

from app.core.security import hash_password
from app.db.session import SessionLocal
from app.models.user import User, UserProfile, UserRole
from app.schemas.auth import Email, check_password_strength
from app.services.auth_service import get_user_by_email


MIN_ADMIN_PASSWORD = 12


def main() -> None:
    if len(sys.argv) != 3:
        sys.exit('Usage: python -m scripts.create_admin <email> "<full name>"')
    name = sys.argv[2].strip()
    try:
        email = TypeAdapter(Email).validate_python(sys.argv[1].strip())  # same rules as login
    except ValidationError as e:
        sys.exit(f"Invalid email: {e.errors()[0]['msg']}")
    password = getpass.getpass("Password: ")
    try:
        check_password_strength(password)
    except ValueError as e:
        sys.exit(str(e))
    if len(password) < MIN_ADMIN_PASSWORD:                 # admins control money and every vendor: demand more than the website's 8 characters
        sys.exit(f"Admin passwords must be at least {MIN_ADMIN_PASSWORD} characters. A short sentence you can remember works well.")
    if password != getpass.getpass("Confirm password: "):
        sys.exit("Passwords do not match")

    with SessionLocal() as db:
        user = get_user_by_email(db, email)
        if user is None:
            user = User(email=email, profile=UserProfile(full_name=name))
            db.add(user)
        user.password_hash = hash_password(password)
        user.role = UserRole.ADMIN
        user.is_email_verified = True
        user.is_active = True
        db.commit()
    print(f"ADMIN ready: {email}")


if __name__ == "__main__":
    main()
