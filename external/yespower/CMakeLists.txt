# Yespower library for ORI (ref-only: deterministic cross-platform)
# yespower-opt.c and yespower-platform.c are intentionally excluded
# because they use SIMD and platform-specific optimizations that would
# produce different hashes on different platforms. Only yespower-ref.c
# is used for deterministic genesis block generation that matches
# across Windows (MSVC) and Linux (GCC/Clang).

add_library(yespower
  yespower-ref.c
  sha256.c)

target_include_directories(yespower PUBLIC ${CMAKE_CURRENT_SOURCE_DIR})
