from fastapi import APIRouter, HTTPException
import autoposter

router = APIRouter(
    prefix="/api/trigger",
    tags=["tasks"]
)

@router.post("")
def trigger_generation():
    try:
        autoposter.run_pipeline(dry_run=False)
        return {"status": "success", "message": "Post generated and published successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
