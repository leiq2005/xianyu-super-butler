@echo off
rem ============================================
rem  Central configuration - edit these values only.
rem  IMAGE_NAME : base name for image / container / archive
rem  IMAGE_TAG  : version; used in container name and .tar name
rem  WEB_PORT   : host port; keep unique per version on one machine
rem  ADMIN_USERNAME : 管理员账号。当前版本后台强制要求为 admin，请勿修改。
rem  ADMIN_PASSWORD : 管理员初始密码。仅在"首次建库"时生效，即
rem                   %~dp0data\xianyu_data.db 不存在的那一次启动。
rem                   库已存在时改这里不会改密码（沿用旧密码），需要改密码请：
rem                   (1) 进入后台「设置 → 账号与同步 → 修改登录密码」；
rem                   (2) 或停止容器后删除 data 目录再启动，会按此处重新建库。
rem                   新机器首次启动请务必改成你自己的密码。
rem ============================================
set "IMAGE_NAME=xianyu-butler-source"
set "IMAGE_TAG=2.2"
set "WEB_PORT=8083"
set "ADMIN_USERNAME=admin"
set "ADMIN_PASSWORD=admin1234"

rem ============================================
rem  Derived values - do not edit below this line.
rem ============================================
set "IMAGE=%IMAGE_NAME%:%IMAGE_TAG%"
set "CONTAINER=%IMAGE_NAME%-%IMAGE_TAG%"
set "ARCHIVE_NAME=%IMAGE_NAME%-%IMAGE_TAG%.tar"
