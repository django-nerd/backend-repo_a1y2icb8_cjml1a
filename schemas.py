"""
Database Schemas

Define your MongoDB collection schemas here using Pydantic models.
These schemas are used for data validation in your application.

Each Pydantic model represents a collection in your database.
Model name is converted to lowercase for the collection name:
- User -> "user" collection
- Product -> "product" collection
- BlogPost -> "blogs" collection
"""

from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime

# Core domain for the app: carpool matching for soldiers

class Soldier(BaseModel):
    """
    Soldiers collection schema
    Collection name: "soldier"
    """
    name: str = Field(..., description="Full name")
    phone: Optional[str] = Field(None, description="Contact phone number")
    home_area: str = Field(..., description="Home town/area (free text, e.g., 'Haifa', 'South Tel Aviv')")
    base_name: str = Field(..., description="Assigned base name or nearest base")
    has_car: bool = Field(False, description="Whether this soldier can drive and offer rides")
    verified: bool = Field(False, description="Verification status")

class Ride(BaseModel):
    """
    Ride offers created by drivers
    Collection name: "ride"
    """
    driver_id: str = Field(..., description="Reference to Soldier _id as string")
    from_area: str = Field(..., description="Start area/city")
    to_area: str = Field(..., description="Destination area/city (e.g., base name)")
    departure_time: datetime = Field(..., description="Planned departure time (ISO datetime)")
    seats_total: int = Field(..., ge=1, le=8, description="Total seats including driver seat not counted")
    seats_available: int = Field(..., ge=0, le=8, description="Seats still available")
    price_per_seat: float = Field(0, ge=0, description="Requested participation per seat")
    car_info: Optional[str] = Field(None, description="Car model/color/plate last 4")
    notes: Optional[str] = Field(None, description="Special notes or pickup points")
    tags: Optional[List[str]] = Field(default_factory=list, description="Searchable tags")

class RideRequest(BaseModel):
    """
    Join requests to a ride
    Collection name: "riderequest"
    """
    ride_id: str = Field(..., description="Ride id as string")
    passenger_id: str = Field(..., description="Soldier id as string")
    seats: int = Field(1, ge=1, le=4, description="Seats requested")
    status: str = Field("pending", description="pending, accepted, rejected, cancelled")
    message: Optional[str] = Field(None, description="Optional note to driver")
