#include <Python.h>
#include <openssl/sha.h>
#include <stdint.h>
#include <string.h>

static inline void sha256_double(const uint8_t* data, uint32_t len, uint8_t* hash) {
    uint8_t tmp[SHA256_DIGEST_LENGTH];
    SHA256(data, len, tmp);
    SHA256(tmp, SHA256_DIGEST_LENGTH, hash);
}

static PyObject* mine_kernel_cpp(PyObject* self, PyObject* args) {
    const uint8_t* static76;
    const uint8_t* target;
    Py_ssize_t static_len, target_len;
    unsigned long long start, end;

    if (!PyArg_ParseTuple(args, "y#y#KK", &static76, &static_len, &target, &target_len, &start, &end)) {
        return NULL;
    }

    uint8_t header[80];
    memcpy(header, static76, 76);
    uint8_t hash[32];

    for (uint64_t nonce = start; nonce < end; ++nonce) {
        // Set nonce in little-endian at the end of the header
        header[76] = (uint8_t)(nonce & 0xFF);
        header[77] = (uint8_t)((nonce >> 8) & 0xFF);
        header[78] = (uint8_t)((nonce >> 16) & 0xFF);
        header[79] = (uint8_t)((nonce >> 24) & 0xFF);

        sha256_double(header, 80, hash);

        // Compare with target (big-endian compare for 256-bit int)
        bool found = true;
        for (int i = 0; i < 32; ++i) {
            if (hash[i] < target[i]) break;
            if (hash[i] > target[i]) {
                found = false;
                break;
            }
        }

        if (found) {
            return PyLong_FromUnsignedLongLong(nonce);
        }
    }

    Py_RETURN_NONE;
}

static PyMethodDef MinerMethods[] = {
    {"mine", mine_kernel_cpp, METH_VARARGS, "Brute-force SHA256d nonce search"},
    {NULL, NULL, 0, NULL}
};

static struct PyModuleDef minermodule = {
    PyModuleDef_HEAD_INIT,
    "miner_kernel",
    NULL,
    -1,
    MinerMethods
};

PyMODINIT_FUNC PyInit_miner_kernel(void) {
    return PyModule_Create(&minermodule);
}
