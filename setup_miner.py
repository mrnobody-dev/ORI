from setuptools import setup, Extension

module = Extension(
    'miner_kernel',
    sources=['miner_kernel.cpp'],
    libraries=['libcrypto'],
    # Update these paths if OpenSSL is in a different location on your Windows machine
    # Typical for OpenSSL-Win64
    include_dirs=['C:/Program Files/OpenSSL-Win64/include'],
    library_dirs=['C:/Program Files/OpenSSL-Win64/lib'],
)

setup(
    name='MinerKernel',
    version='1.0',
    ext_modules=[module],
)
