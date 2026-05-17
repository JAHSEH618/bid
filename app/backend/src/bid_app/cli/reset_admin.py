"""重置 / 创建管理员账号的应急 CLI。

用法(密码传入,按优先级):

    # 1. 最安全:交互式 prompt,输入不回显(默认)
    python -m bid_app.cli.reset_admin

    # 2. 从 stdin 读(管道 / heredoc;CI 可控)
    echo 'new_pass' | python -m bid_app.cli.reset_admin --password-stdin

    # 3. 从环境变量读(适合脚本)
    BID_APP_RESET_ADMIN_PASSWORD=new_pass python -m bid_app.cli.reset_admin --password-env

    # 4. 命令行参数(⚠️ 会暴露在 shell history / ps,只在隔离环境用)
    python -m bid_app.cli.reset_admin --password new_pass

可选 ``--username`` 覆盖目标用户名,默认读 ``ADMIN_DEFAULT_USERNAME``。
"""

from __future__ import annotations

import asyncio
import os
import sys

import click
from sqlalchemy import select

from ..config import settings
from ..core.security import hash_password
from ..db import session_factory
from ..models import User


async def _reset(username: str, password: str) -> int:
    async with session_factory() as s:
        user = (await s.execute(select(User).where(User.username == username))).scalar_one_or_none()
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


def _resolve_password(
    *,
    password: str | None,
    password_stdin: bool,
    password_env: bool,
) -> str:
    """解析密码来源,优先级:--password > --password-stdin > --password-env >
    交互式 prompt。多个互斥来源同时给会用第一个命中的(便于脚本组合)。"""
    if password is not None:
        return password
    if password_stdin:
        # 读一整行(去末尾换行);允许多行的话密码里夹 \n 反而难调试
        raw = sys.stdin.readline()
        if not raw:
            raise click.ClickException("--password-stdin 启用,但 stdin 无内容")
        return raw.rstrip("\r\n")
    if password_env:
        val = os.environ.get("BID_APP_RESET_ADMIN_PASSWORD")
        if not val:
            raise click.ClickException(
                "--password-env 启用,但环境变量 BID_APP_RESET_ADMIN_PASSWORD 未设置"
            )
        return val
    # 交互式:hide_input=True 不回显,confirmation_prompt 二次确认防输错
    prompted = click.prompt(
        "新密码",
        hide_input=True,
        confirmation_prompt=True,
    )
    return str(prompted)


@click.command()
@click.option(
    "--username",
    default=lambda: settings.admin_default_username,
    show_default="ADMIN_DEFAULT_USERNAME",
    help="要重置的管理员用户名",
)
@click.option(
    "--password",
    default=None,
    help="新密码;⚠️ 会进入 shell history / ps,推荐改用 --password-stdin / -env 或不传走交互",
)
@click.option(
    "--password-stdin",
    is_flag=True,
    default=False,
    help="从 stdin 读新密码(只读首行)",
)
@click.option(
    "--password-env",
    is_flag=True,
    default=False,
    help="从环境变量 BID_APP_RESET_ADMIN_PASSWORD 读新密码",
)
def main(
    username: str,
    password: str | None,
    password_stdin: bool,
    password_env: bool,
) -> None:
    resolved = _resolve_password(
        password=password,
        password_stdin=password_stdin,
        password_env=password_env,
    )
    if len(resolved) < 8:
        raise click.ClickException("password must be at least 8 chars")
    user_id = asyncio.run(_reset(username, resolved))
    click.echo(f"admin user ready: username={username}, id={user_id}, must_change_password=true")


if __name__ == "__main__":
    main()
