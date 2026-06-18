/* Yespower PoW for ORI - replaces RandomX
 * Copyright (c) 2024, Mr. Nobody */

#include <stddef.h>
#include <stdint.h>
#include <string.h>

#include "yespower.h"
#include "hash-ops.h"

/* Yespower parameters for ORI:
 * Version: YESPOWER_1_0 (ROM yespower 1.0)
 * N = 2048, r = 32 (moderate memory hardness ~2MB per hash)
 * Personalization: "ORI_PoW:0" (unique to ORI, deterministic) */
#define YESPOWER_N  2048
#define YESPOWER_R  32

static const yespower_params_t yespower_params = {
    YESPOWER_1_0,
    YESPOWER_N,
    YESPOWER_R,
    (const uint8_t *)"ORI_PoW:0",
    9
};

void rx_slow_hash_allocate_state(void)
{
}

void rx_slow_hash_free_state(void)
{
}

uint64_t rx_seedheight(const uint64_t height)
{
    (void)height;
    return 0;
}

void rx_seedheights(const uint64_t height, uint64_t *seed_height, uint64_t *next_height)
{
    (void)height;
    *seed_height = 0;
    *next_height = 0;
}

void rx_set_main_seedhash(const char *seedhash, size_t max_dataset_init_threads)
{
    (void)seedhash;
    (void)max_dataset_init_threads;
}

void rx_slow_hash(const char *seedhash, const void *data, size_t length, char *result_hash)
{
    (void)seedhash;
    yespower_tls((const uint8_t *)data, length, &yespower_params, (yespower_binary_t *)result_hash);
}

void rx_set_miner_thread(uint32_t value, size_t max_dataset_init_threads)
{
    (void)value;
    (void)max_dataset_init_threads;
}

uint32_t rx_get_miner_thread(void)
{
    return 0;
}

void yespower_hash(const void *data, size_t length, char *result_hash)
{
    yespower_tls((const uint8_t *)data, length, &yespower_params, (yespower_binary_t *)result_hash);
}
