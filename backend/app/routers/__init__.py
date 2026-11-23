"""业务路由汇总：所有模块共用一套资源路由，由领域服务完成分派。"""
from __future__ import annotations

from app.routers import join_keys, resource

# 关联键版本接口使用固定前缀，必须先于含 /api/{module} 的资源路由注册。
ROUTERS = [join_keys, resource]
