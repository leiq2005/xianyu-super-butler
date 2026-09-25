@echo off
rem ============================================
rem  Central configuration - edit these three values only.
rem  IMAGE_NAME : base name for image / container / archive
rem  IMAGE_TAG  : version; used in container name and .tar name
rem  WEB_PORT   : host port; keep unique per version on one machine
rem ============================================
set "IMAGE_NAME=xianyu-butler-source"
set "IMAGE_TAG=2.2"
set "WEB_PORT=8083"

rem ============================================
rem  Derived values - do not edit below this line.
rem ============================================
set "IMAGE=%IMAGE_NAME%:%IMAGE_TAG%"
set "CONTAINER=%IMAGE_NAME%-%IMAGE_TAG%"
set "ARCHIVE_NAME=%IMAGE_NAME%-%IMAGE_TAG%.tar"
