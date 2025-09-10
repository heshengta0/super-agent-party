@echo off

:: 激活虚拟环境
call .venv\Scripts\activate.bat

:: 直接运行 npm run dev 而不是在新窗口中
npm run dev

pause