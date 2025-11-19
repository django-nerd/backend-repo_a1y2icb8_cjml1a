import os
from datetime import datetime, timedelta
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from bson import ObjectId

from database import db, create_document, get_documents

app = FastAPI(title="Soldier Carpool API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class SoldierIn(BaseModel):
    name: str
    phone: Optional[str] = None
    home_area: str
    base_name: str
    has_car: bool = False

class SoldierOut(SoldierIn):
    id: str
    verified: bool = False

class RideIn(BaseModel):
    driver_id: str
    from_area: str
    to_area: str
    departure_time: datetime
    seats_total: int
    price_per_seat: float = 0
    car_info: Optional[str] = None
    notes: Optional[str] = None
    tags: Optional[List[str]] = None

class RideOut(RideIn):
    id: str
    seats_available: int

class RideRequestIn(BaseModel):
    ride_id: str
    passenger_id: str
    seats: int = 1
    message: Optional[str] = None

class RideRequestOut(RideRequestIn):
    id: str
    status: str


class MatchRequest(BaseModel):
    soldier_id: str
    window_hours: int = 24

class RideSuggestion(BaseModel):
    ride: RideOut
    score: float


def _to_id_str(doc):
    if not doc:
        return doc
    doc["id"] = str(doc.pop("_id"))
    return doc


@app.get("/")
def read_root():
    return {"message": "Soldier Carpool API running"}


@app.get("/schema")
def get_schema():
    # Expose schemas to the database viewer (as per platform conventions)
    from schemas import Soldier, Ride, RideRequest
    return {
        "soldier": Soldier.model_json_schema(),
        "ride": Ride.model_json_schema(),
        "riderequest": RideRequest.model_json_schema(),
    }


@app.post("/soldiers", response_model=SoldierOut)
def create_soldier(payload: SoldierIn):
    data = payload.model_dump()
    data["verified"] = False
    soldier_id = create_document("soldier", data)
    doc = db["soldier"].find_one({"_id": ObjectId(soldier_id)})
    return _to_id_str(doc)


@app.get("/soldiers", response_model=List[SoldierOut])
def list_soldiers(area: Optional[str] = None, base: Optional[str] = None, has_car: Optional[bool] = None):
    q = {}
    if area:
        q["home_area"] = {"$regex": area, "$options": "i"}
    if base:
        q["base_name"] = {"$regex": base, "$options": "i"}
    if has_car is not None:
        q["has_car"] = has_car
    docs = list(db["soldier"].find(q).sort("created_at", -1).limit(100))
    return [_to_id_str(d) for d in docs]


@app.post("/rides", response_model=RideOut)
def create_ride(payload: RideIn):
    # ensure driver exists
    if not ObjectId.is_valid(payload.driver_id):
        raise HTTPException(status_code=400, detail="Invalid driver_id")
    driver = db["soldier"].find_one({"_id": ObjectId(payload.driver_id)})
    if not driver:
        raise HTTPException(status_code=404, detail="Driver not found")

    data = payload.model_dump()
    data["seats_available"] = payload.seats_total
    ride_id = create_document("ride", data)
    doc = db["ride"].find_one({"_id": ObjectId(ride_id)})
    return _to_id_str(doc)


@app.get("/rides", response_model=List[RideOut])
def list_rides(from_area: Optional[str] = None, to_area: Optional[str] = None, earliest: Optional[datetime] = None):
    q = {}
    if from_area:
        q["from_area"] = {"$regex": from_area, "$options": "i"}
    if to_area:
        q["to_area"] = {"$regex": to_area, "$options": "i"}
    if earliest:
        q["departure_time"] = {"$gte": earliest}
    docs = list(db["ride"].find(q).sort("departure_time", 1).limit(100))
    return [_to_id_str(d) for d in docs]


@app.post("/ride-requests", response_model=RideRequestOut)
def create_ride_request(payload: RideRequestIn):
    # validate ride and passenger
    if not ObjectId.is_valid(payload.ride_id) or not ObjectId.is_valid(payload.passenger_id):
        raise HTTPException(status_code=400, detail="Invalid ids")
    ride = db["ride"].find_one({"_id": ObjectId(payload.ride_id)})
    if not ride:
        raise HTTPException(status_code=404, detail="Ride not found")
    passenger = db["soldier"].find_one({"_id": ObjectId(payload.passenger_id)})
    if not passenger:
        raise HTTPException(status_code=404, detail="Passenger not found")
    if payload.seats < 1:
        raise HTTPException(status_code=400, detail="Seats must be >= 1")
    if ride.get("seats_available", 0) < payload.seats:
        raise HTTPException(status_code=400, detail="Not enough seats available")

    data = payload.model_dump()
    data["status"] = "pending"
    req_id = create_document("riderequest", data)
    doc = db["riderequest"].find_one({"_id": ObjectId(req_id)})
    return _to_id_str(doc)


class UpdateRequestStatus(BaseModel):
    status: str


@app.post("/ride-requests/{request_id}/status", response_model=RideRequestOut)
def update_request_status(request_id: str, payload: UpdateRequestStatus):
    if payload.status not in {"pending", "accepted", "rejected", "cancelled"}:
        raise HTTPException(status_code=400, detail="Invalid status")
    if not ObjectId.is_valid(request_id):
        raise HTTPException(status_code=400, detail="Invalid request id")

    req = db["riderequest"].find_one({"_id": ObjectId(request_id)})
    if not req:
        raise HTTPException(status_code=404, detail="Request not found")

    # If accepting, decrease available seats
    if req["status"] != "accepted" and payload.status == "accepted":
        ride = db["ride"].find_one({"_id": ObjectId(req["ride_id"])})
        if not ride:
            raise HTTPException(status_code=404, detail="Ride not found")
        if ride.get("seats_available", 0) < req["seats"]:
            raise HTTPException(status_code=400, detail="Not enough seats available")
        db["ride"].update_one({"_id": ObjectId(req["ride_id"])}, {"$inc": {"seats_available": -req["seats"]}})

    # If moving from accepted back to other status, return seats
    if req["status"] == "accepted" and payload.status in {"rejected", "cancelled"}:
        db["ride"].update_one({"_id": ObjectId(req["ride_id"])}, {"$inc": {"seats_available": req["seats"]}})

    db["riderequest"].update_one({"_id": ObjectId(request_id)}, {"$set": {"status": payload.status}})
    updated = db["riderequest"].find_one({"_id": ObjectId(request_id)})
    return _to_id_str(updated)


@app.get("/ride-requests", response_model=List[RideRequestOut])
def list_requests(ride_id: Optional[str] = None, passenger_id: Optional[str] = None, status: Optional[str] = None):
    q = {}
    if ride_id and ObjectId.is_valid(ride_id):
        q["ride_id"] = ride_id
    if passenger_id and ObjectId.is_valid(passenger_id):
        q["passenger_id"] = passenger_id
    if status:
        q["status"] = status
    docs = list(db["riderequest"].find(q).sort("created_at", -1).limit(100))
    return [_to_id_str(d) for d in docs]


@app.post("/ai/suggest-rides", response_model=List[RideSuggestion])
def suggest_rides(payload: MatchRequest):
    # Validate soldier
    if not ObjectId.is_valid(payload.soldier_id):
        raise HTTPException(status_code=400, detail="Invalid soldier_id")
    soldier = db["soldier"].find_one({"_id": ObjectId(payload.soldier_id)})
    if not soldier:
        raise HTTPException(status_code=404, detail="Soldier not found")

    # Time window
    now = datetime.utcnow()
    latest = now + timedelta(hours=max(1, min(payload.window_hours, 168)))

    # Fetch upcoming rides
    query = {"departure_time": {"$gte": now, "$lte": latest}, "seats_available": {"$gt": 0}}
    rides = list(db["ride"].find(query).limit(200))

    def score_ride(ride):
        score = 0.0
        # Origin/destination affinity
        if soldier.get("home_area") and ride.get("from_area"):
            if soldier["home_area"].lower() in ride["from_area"].lower() or ride["from_area"].lower() in soldier["home_area"].lower():
                score += 5
        if soldier.get("base_name") and ride.get("to_area"):
            if soldier["base_name"].lower() in ride["to_area"].lower() or ride["to_area"].lower() in soldier["base_name"].lower():
                score += 5
        # Time proximity (closer is better)
        try:
            dt = ride.get("departure_time")
            if isinstance(dt, str):
                dt = datetime.fromisoformat(dt)
            delta_hours = abs((dt - now).total_seconds()) / 3600.0
            score += max(0, 6 - min(delta_hours, 6))  # up to +6 if within hours
        except Exception:
            pass
        # Seats available bonus
        score += min(ride.get("seats_available", 0), 4) * 0.5
        # Price sensitivity (cheaper better)
        price = ride.get("price_per_seat", 0) or 0
        score += max(0, 3 - min(price / 10.0, 3))  # +3 if free, taper off with price
        return score

    scored = [
        {"ride": _to_id_str(r.copy()), "score": round(score_ride(r), 2)} for r in rides
    ]
    scored.sort(key=lambda x: x["score"], reverse=True)
    # Convert to Pydantic-friendly output with RideOut
    result: List[RideSuggestion] = []
    for item in scored[:50]:
        ride_doc = item["ride"]
        # Ensure keys match RideOut fields
        ride_out = RideOut(**{
            "id": ride_doc["id"],
            "driver_id": ride_doc.get("driver_id"),
            "from_area": ride_doc.get("from_area"),
            "to_area": ride_doc.get("to_area"),
            "departure_time": ride_doc.get("departure_time"),
            "seats_total": ride_doc.get("seats_total"),
            "price_per_seat": ride_doc.get("price_per_seat", 0),
            "car_info": ride_doc.get("car_info"),
            "notes": ride_doc.get("notes"),
            "tags": ride_doc.get("tags"),
            "seats_available": ride_doc.get("seats_available", 0),
        })
        result.append(RideSuggestion(ride=ride_out, score=item["score"]))
    return result


@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        from database import db
        if db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Configured"
            response["database_name"] = db.name if hasattr(db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:50]}"

    import os
    response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
    response["database_name"] = "✅ Set" if os.getenv("DATABASE_NAME") else "❌ Not Set"

    return response


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
