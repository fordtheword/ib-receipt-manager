@echo off
echo Building llama-cpp-python with CUDA support...
set CUDA_PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8
set CudaToolkitDir=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8
set PATH=C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin;%PATH%
set CMAKE_ARGS=-DGGML_CUDA=on

echo CUDA_PATH=%CUDA_PATH%
echo CudaToolkitDir=%CudaToolkitDir%

"%~dp0venv\Scripts\pip.exe" install llama-cpp-python==0.3.16 --no-cache-dir --force-reinstall
echo.
echo Done! Exit code: %ERRORLEVEL%
pause
