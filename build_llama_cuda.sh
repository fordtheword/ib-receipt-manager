#!/bin/bash
echo "Building llama-cpp-python with CUDA support..."

# Adjust these paths if your CUDA installation is different
export CUDA_PATH=/usr/local/cuda
export CudaToolkitDir=/usr/local/cuda
export PATH=/usr/local/cuda/bin:$PATH
export CMAKE_ARGS="-DGGML_CUDA=on"

echo "CUDA_PATH=$CUDA_PATH"
echo "CudaToolkitDir=$CudaToolkitDir"

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Run pip from the virtual environment
"$DIR/venv/bin/pip" install llama-cpp-python==0.3.16 --no-cache-dir --force-reinstall

echo ""
echo "Done! Exit code: $?"
