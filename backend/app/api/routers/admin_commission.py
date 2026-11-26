"""Admin API for managing vendor and rider commissions."""
from decimal import Decimal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from app.api.deps import get_current_user, get_db
from app.core.config import get_settings
from app.core.errors import api_error
from app.models.user import User
from sqlalchemy.orm import Session

router = APIRouter()


class CommissionUpdate(BaseModel):
    vendor_commission_percent: float = Field(ge=0, le=50, description="Platform commission on vendor sales (0-50%)")
    rider_share_percent: int = Field(ge=0, le=100, description="Rider's share of delivery fee (0-100%)")


@router.get("/commission-settings")
def get_commission_settings(
    current_user: User = Depends(get_current_user),
):
    """Get current commission settings."""
    if current_user.role.value != "ADMIN":
        raise api_error(403, "forbidden", "Only admins can view commission settings.")
    
    settings = get_settings()
    
    return {
        "vendor_commission_percent": settings.vendor_commission_percent,
        "rider_share_percent": settings.rider_share_percent,
        "description": {
            "vendor_commission": f"Platform takes {settings.vendor_commission_percent}% of vendor food sales (after vendor-funded discounts)",
            "rider_share": f"Rider keeps {settings.rider_share_percent}% of delivery fee, platform keeps {100 - settings.rider_share_percent}%"
        }
    }


@router.post("/commission-settings")
def update_commission_settings(
    data: CommissionUpdate,
    current_user: User = Depends(get_current_user),
):
    """Update commission settings. These are stored in environment variables and require server restart."""
    if current_user.role.value != "ADMIN":
        raise api_error(403, "forbidden", "Only admins can update commission settings.")
    
    # Note: In production, these should be stored in a database table
    # For now, we'll update the .env file (requires server restart)
    from pathlib import Path
    import os
    
    env_path = Path(__file__).resolve().parents[5] / "backend" / ".env"
    
    if not env_path.exists():
        raise api_error(500, "config_error", "Cannot find .env file to update.")
    
    # Read current .env
    with open(env_path, "r") as f:
        lines = f.readlines()
    
    # Update commission settings
    updated_lines = []
    for line in lines:
        if line.startswith("VENDOR_COMMISSION_PERCENT="):
            updated_lines.append(f"VENDOR_COMMISSION_PERCENT={data.vendor_commission_percent}\n")
        elif line.startswith("RIDER_SHARE_PERCENT="):
            updated_lines.append(f"RIDER_SHARE_PERCENT={data.rider_share_percent}\n")
        else:
            updated_lines.append(line)
    
    # Add new lines if not present
    has_vendor = any("VENDOR_COMMISSION_PERCENT=" in line for line in updated_lines)
    has_rider = any("RIDER_SHARE_PERCENT=" in line for line in updated_lines)
    
    if not has_vendor:
        updated_lines.append(f"VENDOR_COMMISSION_PERCENT={data.vendor_commission_percent}\n")
    if not has_rider:
        updated_lines.append(f"RIDER_SHARE_PERCENT={data.rider_share_percent}\n")
    
    # Write back
    with open(env_path, "w") as f:
        f.writelines(updated_lines)
    
    return {
        "message": "Commission settings updated. Server restart required for changes to take effect.",
        "new_settings": {
            "vendor_commission_percent": data.vendor_commission_percent,
            "rider_share_percent": data.rider_share_percent
        }
    }
