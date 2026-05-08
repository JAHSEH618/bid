"""重置 / 创建管理员账号的应急 CLI。

用法:

    python -m bid_app.cli.reset_admin --password new_pass
    python -m bid_app.cli.reset_admin --username admin --password new_pass
"""
from __future__ import annotations

import asyncio

import click
from sqlalchemy import select

from ..config import settings
from ..core.security import hash_password
from ..db import session_factory
from ..models import User


async def _reset(username: str, password: str) -> int:
    async with session_factory() as s:
        user = (
            await s.execute(select(User).where(User.username == username))
        ).scalar_one_or_none()
        if user is None:
            user = User(
                username=username,
                password_hash=hash_password(password),
                role="admin",
                is_active=True,
                must_change_password=True,
            )
            s.add(user)
            await s.commit()
            await s.refresh(user)
            return user.id

        user.password_hash = hash_password(password)
        user.role = "admin"
        user.is_active = True
        user.must_change_password = True
        await s.commit()
        return user.id


@click.command()
@click.option(
    "--username",
    default=lambda: settings.admin_default_username,
    show_default="ADMIN_DEFAULT_USERNAME",
    help="要重置的管理员用户名",
)
@click.option(
    "--password",
    required=True,
    help="新的临时密码;登录后会强制修改",
)
def main(username: str, password: str) -> None:
    if len(password) < 8:
        raise click.ClickException("password must be at least 8 chars")
    user_id = asyncio.run(_reset(username, password))
    click.echo(
        f"admin user ready: username={username}, id={user_id}, "
        "must_change_password=true"
    )


if __name__ == "__main__":
    main()
