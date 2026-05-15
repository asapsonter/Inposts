from fastapi import APIRouter
from db import init_db, get_posts

router = APIRouter(
    prefix="/api/posts",
    tags=["posts"]
)

@router.get("")
def get_all_posts():
    conn = init_db()
    posts = get_posts(conn)
    conn.close()
    return {"posts": posts}
