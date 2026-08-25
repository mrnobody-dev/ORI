#ifndef UNICODE
#define UNICODE
#endif
#ifndef _UNICODE
#define _UNICODE
#endif

#include <windows.h>
#include <winhttp.h>
#include <bcrypt.h>

#include <iostream>
#include <vector>
#include <string>
#include <thread>
#include <atomic>
#include <chrono>
#include <sstream>
#include <iomanip>
#include <cstdint>
#include <algorithm>

#pragma comment(lib, "winhttp.lib")
#pragma comment(lib, "bcrypt.lib")

static std::atomic<bool> g_stop_mining(false);
static std::atomic<uint64_t> g_total_hashes(0);

static const char* C_RESET = "\033[0m";
static const char* C_RED = "\033[1;31m";
static const char* C_GREEN = "\033[1;32m";
static const char* C_YELLOW = "\033[1;33m";
static const char* C_BLUE = "\033[1;34m";
static const char* C_CYAN = "\033[1;36m";
static const char* C_DIM = "\033[2m";

// Simple JSON helper
std::string json_extract_string(const std::string& json, const std::string& key) {
    size_t pos = json.find("\"" + key + "\"");
    if (pos == std::string::npos) return "";
    pos = json.find(":", pos);
    if (pos == std::string::npos) return "";
    size_t start = json.find("\"", pos);
    if (start == std::string::npos) return "";
    size_t end = json.find("\"", start + 1);
    if (end == std::string::npos) return "";
    return json.substr(start + 1, end - start - 1);
}

int64_t json_extract_int64(const std::string& json, const std::string& key) {
    size_t pos = json.find("\"" + key + "\"");
    if (pos == std::string::npos) return 0;
    pos = json.find(":", pos);
    if (pos == std::string::npos) return 0;
    size_t start = json.find_first_of("0123456789-", pos);
    if (start == std::string::npos) return 0;
    size_t end = json.find_first_not_of("0123456789", start + (json[start] == '-' ? 1 : 0));
    if (end == std::string::npos) end = json.length();
    return std::stoll(json.substr(start, end - start));
}

std::vector<std::string> json_extract_string_array(const std::string& json, const std::string& key) {
    std::vector<std::string> result;
    size_t pos = json.find("\"" + key + "\"");
    if (pos == std::string::npos) return result;
    pos = json.find("[", pos);
    if (pos == std::string::npos) return result;
    size_t end_arr = json.find("]", pos);
    if (end_arr == std::string::npos) return result;
    
    std::string arr_str = json.substr(pos + 1, end_arr - pos - 1);
    size_t cur = 0;
    while ((cur = arr_str.find("\"", cur)) != std::string::npos) {
        size_t next = arr_str.find("\"", cur + 1);
        if (next == std::string::npos) break;
        result.push_back(arr_str.substr(cur + 1, next - cur - 1));
        cur = next + 1;
    }
    return result;
}

double target_to_difficulty(const std::vector<uint8_t>& target) {
    static const long double max_target = 0x0000FFFFp+208L;
    long double t = 0.0L;
    for (uint8_t b : target) {
        t = t * 256.0L + (long double)b;
    }
    if (t <= 0.0L) return 0.0;
    return (double)(max_target / t);
}

std::vector<uint8_t> hex_to_bytes(const std::string& hex) {
    std::vector<uint8_t> bytes;
    for (size_t i = 0; i < hex.length(); i += 2) {
        if (i + 1 >= hex.length()) break;
        std::string byteString = hex.substr(i, 2);
        uint8_t byte = (uint8_t) strtol(byteString.c_str(), nullptr, 16);
        bytes.push_back(byte);
    }
    return bytes;
}

std::string bytes_to_hex(const uint8_t* data, size_t len) {
    std::stringstream ss;
    for (size_t i = 0; i < len; ++i) {
        ss << std::hex << std::setw(2) << std::setfill('0') << (int)data[i];
    }
    return ss.str();
}

std::wstring to_wstring(const std::string& str) {
    if (str.empty()) return L"";
    int count = MultiByteToWideChar(CP_UTF8, 0, str.c_str(), (int)str.length(), NULL, 0);
    std::wstring wstr(count, 0);
    MultiByteToWideChar(CP_UTF8, 0, str.c_str(), (int)str.length(), &wstr[0], count);
    return wstr;
}

// Windows CNG SHA256 Implementation
class CNGSHA256 {
    BCRYPT_ALG_HANDLE hAlg;
public:
    CNGSHA256() {
        BCryptOpenAlgorithmProvider(&hAlg, BCRYPT_SHA256_ALGORITHM, NULL, 0);
    }
    ~CNGSHA256() {
        if (hAlg) BCryptCloseAlgorithmProvider(hAlg, 0);
    }
    
    void hash(const uint8_t* data, size_t len, uint8_t* out) {
        BCRYPT_HASH_HANDLE hHash = NULL;
        BCryptCreateHash(hAlg, &hHash, NULL, 0, NULL, 0, 0);
        BCryptHashData(hHash, (PUCHAR)data, (ULONG)len, 0);
        BCryptFinishHash(hHash, out, 32, 0);
        BCryptDestroyHash(hHash);
    }

    void sha256d(const uint8_t* data, size_t len, uint8_t* out) {
        uint8_t tmp[32];
        hash(data, len, tmp);
        hash(tmp, 32, out);
    }
};

struct HTTPResult {
    int status;
    std::string body;
    DWORD error;
};

std::string url_encode(const std::string& s) {
    std::ostringstream escaped;
    escaped.fill('0');
    escaped << std::hex;
    for (unsigned char c : s) {
        if (isalnum(c) || c == '-' || c == '_' || c == '.' || c == '~') {
            escaped << c;
        } else {
            escaped << '%' << std::uppercase << std::setw(2) << int(c) << std::nouppercase;
        }
    }
    return escaped.str();
}

HTTPResult http_request(const std::string& host, int port, const std::string& path, const std::string& method, const std::string& post_data = "", const std::string& token = "", bool https = false) {
    HTTPResult res = {0, "", 0};
    HINTERNET hSession = WinHttpOpen(L"ORI-CPP-Miner/1.0", WINHTTP_ACCESS_TYPE_DEFAULT_PROXY, WINHTTP_NO_PROXY_NAME, WINHTTP_NO_PROXY_BYPASS, 0);
    if (!hSession) return res;

    HINTERNET hConnect = WinHttpConnect(hSession, to_wstring(host).c_str(), (INTERNET_PORT)port, 0);
    if (!hConnect) { WinHttpCloseHandle(hSession); return res; }

    DWORD flags = https ? WINHTTP_FLAG_SECURE : 0;
    HINTERNET hRequest = WinHttpOpenRequest(hConnect, to_wstring(method).c_str(), to_wstring(path).c_str(), NULL, WINHTTP_NO_REFERER, WINHTTP_DEFAULT_ACCEPT_TYPES, flags);
    if (!hRequest) { res.error = GetLastError(); WinHttpCloseHandle(hConnect); WinHttpCloseHandle(hSession); return res; }

    std::wstring headers = L"Content-Type: application/json\r\n";
    if (!token.empty()) {
        headers += L"X-API-Key: " + to_wstring(token) + L"\r\n";
    }

    if (https) {
        DWORD security_flags = SECURITY_FLAG_IGNORE_UNKNOWN_CA |
            SECURITY_FLAG_IGNORE_CERT_WRONG_USAGE |
            SECURITY_FLAG_IGNORE_CERT_CN_INVALID |
            SECURITY_FLAG_IGNORE_CERT_DATE_INVALID;
        WinHttpSetOption(hRequest, WINHTTP_OPTION_SECURITY_FLAGS,
                         &security_flags, sizeof(security_flags));
    }

    BOOL bResults = WinHttpSendRequest(
        hRequest, headers.c_str(), (DWORD)headers.length(),
        (LPVOID)(post_data.empty() ? NULL : post_data.c_str()),
        (DWORD)post_data.length(), (DWORD)post_data.length(), 0);
    if (!bResults) res.error = GetLastError();
    if (bResults) bResults = WinHttpReceiveResponse(hRequest, NULL);
    if (!bResults && !res.error) res.error = GetLastError();

    if (bResults) {
        DWORD dwStatusCode = 0;
        DWORD dwSize = sizeof(dwStatusCode);
        WinHttpQueryHeaders(hRequest, WINHTTP_QUERY_STATUS_CODE | WINHTTP_QUERY_FLAG_NUMBER, WINHTTP_HEADER_NAME_BY_INDEX, &dwStatusCode, &dwSize, WINHTTP_NO_HEADER_INDEX);
        res.status = (int)dwStatusCode;

        DWORD dwDownloaded = 0;
        do {
            dwSize = 0;
            if (!WinHttpQueryDataAvailable(hRequest, &dwSize)) break;
            if (dwSize == 0) break;

            std::vector<char> pszOutBuffer(dwSize + 1, 0);
            if (WinHttpReadData(hRequest, (LPVOID)pszOutBuffer.data(), dwSize, &dwDownloaded)) {
                res.body.append(pszOutBuffer.data(), dwDownloaded);
            }
        } while (dwSize > 0);
    }

    WinHttpCloseHandle(hRequest);
    WinHttpCloseHandle(hConnect);
    WinHttpCloseHandle(hSession);
    return res;
}

std::vector<uint8_t> varint_encode(uint64_t val) {
    std::vector<uint8_t> res;
    if (val < 0xfd) {
        res.push_back((uint8_t)val);
    } else if (val <= 0xffff) {
        res.push_back(0xfd);
        res.push_back(val & 0xff);
        res.push_back((val >> 8) & 0xff);
    } else if (val <= 0xffffffff) {
        res.push_back(0xfe);
        for (int i = 0; i < 4; ++i) res.push_back((val >> (i * 8)) & 0xff);
    } else {
        res.push_back(0xff);
        for (int i = 0; i < 8; ++i) res.push_back((val >> (i * 8)) & 0xff);
    }
    return res;
}

std::vector<uint8_t> build_coinbase(int64_t height, int64_t reward_sats, const std::string& address) {
    std::vector<uint8_t> tx;
    uint32_t version = 1;
    tx.insert(tx.end(), (uint8_t*)&version, (uint8_t*)&version + 4);

    tx.push_back(1); // 1 input

    for (int i = 0; i < 32; ++i) tx.push_back(0); // prev_txid
    uint32_t vout = 0xFFFFFFFF;
    tx.insert(tx.end(), (uint8_t*)&vout, (uint8_t*)&vout + 4);

    // Height encoding
    std::vector<uint8_t> hbytes;
    int64_t tmp_h = height;
    while (tmp_h > 0) {
        hbytes.push_back(tmp_h & 0xff);
        tmp_h >>= 8;
    }
    if (hbytes.empty()) hbytes.push_back(0);

    std::vector<uint8_t> script;
    script.push_back((uint8_t)hbytes.size());
    script.insert(script.end(), hbytes.begin(), hbytes.end());

    std::vector<uint8_t> v_ssize = varint_encode(script.size());
    tx.insert(tx.end(), v_ssize.begin(), v_ssize.end());
    tx.insert(tx.end(), script.begin(), script.end());

    uint32_t seq = 0xFFFFFFFF;
    tx.insert(tx.end(), (uint8_t*)&seq, (uint8_t*)&seq + 4);

    tx.push_back(1); // 1 output
    tx.insert(tx.end(), (uint8_t*)&reward_sats, (uint8_t*)&reward_sats + 8);

    std::vector<uint8_t> spk(address.begin(), address.end());
    std::vector<uint8_t> v_spk = varint_encode(spk.size());
    tx.insert(tx.end(), v_spk.begin(), v_spk.end());
    tx.insert(tx.end(), spk.begin(), spk.end());

    uint32_t locktime = 0;
    tx.insert(tx.end(), (uint8_t*)&locktime, (uint8_t*)&locktime + 4);

    return tx;
}

std::vector<uint8_t> compute_merkle_root(CNGSHA256& hasher, std::vector<std::vector<uint8_t>>& txids) {
    if (txids.empty()) return std::vector<uint8_t>(32, 0);
    std::vector<std::vector<uint8_t>> level = txids;
    while (level.size() > 1) {
        if (level.size() % 2 != 0) {
            level.push_back(level.back());
        }
        std::vector<std::vector<uint8_t>> next_level;
        for (size_t i = 0; i < level.size(); i += 2) {
            std::vector<uint8_t> concat = level[i];
            concat.insert(concat.end(), level[i + 1].begin(), level[i + 1].end());
            std::vector<uint8_t> hash(32);
            hasher.sha256d(concat.data(), concat.size(), hash.data());
            next_level.push_back(hash);
        }
        level = next_level;
    }
    return level[0];
}

bool meets_target(const uint8_t* hash, const uint8_t* target) {
    for (int i = 0; i < 32; ++i) {
        if (hash[i] < target[i]) return true;
        if (hash[i] > target[i]) return false;
    }
    return true;
}

// Per-round state — passed by pointer so each round has its own isolated copy.
struct RoundState {
    std::atomic<uint32_t> winning_nonce{0};
    std::atomic<bool>     found{false};
    std::atomic<bool>     stop{false};       // round-local stop flag
    std::atomic<uint64_t> total_hashes{0};  // round-local hash counter
};

void pow_worker(
    std::vector<uint8_t> static76,
    std::vector<uint8_t> target_bytes,
    uint32_t start_nonce,
    uint32_t stride,
    RoundState* rs
) {
    CNGSHA256 hasher;
    uint8_t header[80];
    memcpy(header, static76.data(), 76);
    uint8_t hash[32];

    uint64_t nonce = start_nonce;
    uint64_t local_hashes = 0;

    while (!rs->stop.load(std::memory_order_relaxed)
           && !rs->found.load(std::memory_order_relaxed)
           && nonce <= 0xFFFFFFFF) {

        header[76] = (uint8_t)(nonce & 0xFF);
        header[77] = (uint8_t)((nonce >> 8) & 0xFF);
        header[78] = (uint8_t)((nonce >> 16) & 0xFF);
        header[79] = (uint8_t)((nonce >> 24) & 0xFF);

        hasher.sha256d(header, 80, hash);
        local_hashes++;

        if (local_hashes >= 65536) {
            rs->total_hashes.fetch_add(local_hashes, std::memory_order_relaxed);
            local_hashes = 0;

            // Also check global stop (e.g. Ctrl-C)
            if (g_stop_mining.load(std::memory_order_relaxed)) {
                rs->stop.store(true, std::memory_order_relaxed);
                break;
            }
        }

        if (meets_target(hash, target_bytes.data())) {
            rs->winning_nonce.store((uint32_t)nonce, std::memory_order_relaxed);
            rs->found.store(true, std::memory_order_release); // release so monitor sees nonce
            rs->stop.store(true, std::memory_order_relaxed);
            break;
        }

        nonce += stride;
    }
    rs->total_hashes.fetch_add(local_hashes, std::memory_order_relaxed);
}

int main(int argc, char* argv[]) {
    std::string host = "127.0.0.1";
    int port = 8000;
    std::string address = "";
    // Default = all logical cores; overridden by --threads after parsing.
    int hw_cores = (int)std::thread::hardware_concurrency();
    if (hw_cores < 1) hw_cores = 1;
    int threads = hw_cores; // will be clamped after arg parsing
    std::string token = "";
    bool pool_mode = false;  // --pool: connect to PPLNS pool instead of node directly

    bool https = false;
    for (int i = 1; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--node" && i + 1 < argc) {
            std::string node = argv[++i];
            if (node.rfind("https://", 0) == 0) {
                https = true;
                node = node.substr(8);
            } else if (node.rfind("http://", 0) == 0) {
                https = false;
                node = node.substr(7);
            }
            while (!node.empty() && node.back() == '/') node.pop_back();
            size_t colon = node.rfind(':');
            if (colon != std::string::npos) {
                host = node.substr(0, colon);
                port = std::stoi(node.substr(colon + 1));
            } else {
                host = node;
                port = https ? 443 : 80;
            }
        }
        else if (arg == "--host" && i + 1 < argc) host = argv[++i];
        else if (arg == "--port" && i + 1 < argc) port = std::stoi(argv[++i]);
        else if (arg == "--address" && i + 1 < argc) address = argv[++i];
        else if ((arg == "--threads" || arg == "--thread") && i + 1 < argc) {
            int t = std::stoi(argv[++i]);
            threads = std::max(1, std::min(t, hw_cores));
        }
        else if ((arg == "--token" || arg == "--api-token") && i + 1 < argc) token = argv[++i];
        else if (arg == "--https") https = true;
        else if (arg == "--pool") pool_mode = true;
    }

    if (address.empty()) {
        std::cout << "Usage: miner-ori --address <payout_address> [options]" << std::endl;
        std::cout << "  --host HOST      Pool/node host (default 127.0.0.1)" << std::endl;
        std::cout << "  --port PORT      Pool/node port (default 8000)" << std::endl;
        std::cout << "  --threads N      Number of CPU threads" << std::endl;
        std::cout << "  --token TOKEN    API token" << std::endl;
        std::cout << "  --pool           Pool mode: connect to PPLNS pool server" << std::endl;
        std::cout << "  --https          Use HTTPS" << std::endl;
        return 1;
    }

    std::cout << C_CYAN << " * ORI Native Miner " << C_RESET << "v1.1.0" << std::endl;
    std::cout << C_DIM  << " * " << (pool_mode ? "PPLNS pool mode" : "solo mining mode") << C_RESET << std::endl;
    std::cout << C_BLUE << " * " << (pool_mode ? "pool" : "node") << C_RESET
              << "    " << host << ":" << port << (https ? " tls" : "") << std::endl;
    std::cout << C_BLUE << " * payout" << C_RESET << "  " << address << std::endl;
    std::cout << C_BLUE << " * cpu   " << C_RESET << "  " << threads << " threads" << std::endl;
    if (pool_mode)
        std::cout << C_DIM << " * 1% pool fee, PPLNS payouts after 100-block maturity" << C_RESET << std::endl;

    CNGSHA256 hasher;
    uint64_t accepted_blocks = 0;

    // Per-round pool difficulty (updated after each accepted share via vardiff)
    std::string pool_current_target_hex = "";
    uint64_t    accepted_shares = 0;

    while (true) {
        g_stop_mining.store(false);

        // ── POOL MODE ────────────────────────────────────────────────────────
        if (pool_mode) {
            std::string job_path = "/pool/job?worker=" + url_encode(address);
            HTTPResult job_res = http_request(host, port, job_path, "GET", "", token, https);
            if (job_res.status != 200) {
                std::cout << "[ERROR] Cannot fetch pool job (HTTP " << job_res.status
                          << "): " << job_res.body << ". Retrying in 3s..." << std::endl;
                std::this_thread::sleep_for(std::chrono::seconds(3));
                continue;
            }

            std::string json_job   = job_res.body;
            std::string job_id     = json_extract_string(json_job, "job_id");
            int64_t     height     = json_extract_int64(json_job, "height");
            int64_t     reward     = json_extract_int64(json_job, "reward_sats");
            uint32_t    bits       = (uint32_t)json_extract_int64(json_job, "bits");
            uint32_t    timestamp  = (uint32_t)json_extract_int64(json_job, "timestamp");
            std::string prev_hash_hex     = json_extract_string(json_job, "prev_hash");
            std::string coinbase_address  = json_extract_string(json_job, "coinbase_address");
            std::string pool_target_hex   = json_extract_string(json_job, "pool_target");
            std::string node_target_hex   = json_extract_string(json_job, "node_target");

            if (coinbase_address.empty() || pool_target_hex.empty()) {
                std::cout << "[ERROR] Invalid pool job response. Retrying in 3s..." << std::endl;
                std::this_thread::sleep_for(std::chrono::seconds(3));
                continue;
            }

            // Normalize pool target
            if (pool_target_hex.rfind("0x", 0) == 0) pool_target_hex = pool_target_hex.substr(2);
            while (pool_target_hex.length() < 64) pool_target_hex = "0" + pool_target_hex;
            std::vector<uint8_t> pool_target_bytes = hex_to_bytes(pool_target_hex);

            // Build coinbase with POOL address (not worker address)
            std::vector<uint8_t> coinbase_raw = build_coinbase(height, reward, coinbase_address);
            std::vector<uint8_t> cb_txid(32);
            hasher.sha256d(coinbase_raw.data(), coinbase_raw.size(), cb_txid.data());

            std::vector<std::vector<uint8_t>> txids = { cb_txid };
            std::vector<std::string> mempool_txs = json_extract_string_array(json_job, "txs");
            for (const auto& tx_hex : mempool_txs) {
                std::vector<uint8_t> tx_raw = hex_to_bytes(tx_hex);
                std::vector<uint8_t> tid(32);
                hasher.sha256d(tx_raw.data(), tx_raw.size(), tid.data());
                txids.push_back(tid);
            }

            std::vector<uint8_t> merkle_root = compute_merkle_root(hasher, txids);
            std::vector<uint8_t> prev_hash   = hex_to_bytes(prev_hash_hex);
            if (prev_hash.size() != 32) {
                std::cout << "[ERROR] Invalid prev_hash. Refreshing..." << std::endl;
                continue;
            }
            std::reverse(prev_hash.begin(), prev_hash.end());

            std::vector<uint8_t> static76;
            int32_t version = 1;
            static76.insert(static76.end(), (uint8_t*)&version, (uint8_t*)&version + 4);
            static76.insert(static76.end(), prev_hash.begin(), prev_hash.end());
            static76.insert(static76.end(), merkle_root.begin(), merkle_root.end());
            static76.insert(static76.end(), (uint8_t*)&timestamp, (uint8_t*)&timestamp + 4);
            static76.insert(static76.end(), (uint8_t*)&bits, (uint8_t*)&bits + 4);

            double pool_diff = target_to_difficulty(pool_target_bytes);
            std::cout << C_BLUE << "[pool]" << C_RESET
                      << " job " << C_DIM << job_id << C_RESET
                      << " height " << C_CYAN << height << C_RESET
                      << " diff " << C_YELLOW << std::fixed << std::setprecision(4) << pool_diff << C_RESET
                      << " coinbase→" << C_DIM << coinbase_address.substr(0, 12) << "..." << C_RESET
                      << std::endl;

            RoundState rs;
            std::vector<std::thread> workers;
            workers.reserve(threads);
            auto t_start = std::chrono::high_resolution_clock::now();

            for (int i = 0; i < threads; ++i)
                workers.emplace_back(pow_worker, static76, pool_target_bytes,
                                     (uint32_t)i, (uint32_t)threads, &rs);

            for (int sec = 0; sec < 30; ++sec) {
                std::this_thread::sleep_for(std::chrono::seconds(1));
                if (rs.found.load(std::memory_order_acquire)) break;
                if (g_stop_mining.load(std::memory_order_relaxed)) { rs.stop.store(true); break; }
                auto   t_now = std::chrono::high_resolution_clock::now();
                double elapsed = std::chrono::duration<double>(t_now - t_start).count();
                double mh = (rs.total_hashes.load(std::memory_order_relaxed) / 1e6)
                            / (elapsed > 0 ? elapsed : 1.0);
                std::cout << "\r" << C_CYAN << "[cpu ]" << C_RESET
                          << " threads " << threads
                          << " | " << C_YELLOW << std::fixed << std::setprecision(2) << mh << " MH/s" << C_RESET
                          << " | shares " << C_GREEN << accepted_shares << C_RESET
                          << "    " << std::flush;
            }

            rs.stop.store(true, std::memory_order_relaxed);
            for (auto& w : workers) if (w.joinable()) w.join();

            if (!rs.found.load(std::memory_order_acquire)) continue;

            uint32_t nonce = rs.winning_nonce.load(std::memory_order_relaxed);
            std::vector<uint8_t> header80 = static76;
            header80.insert(header80.end(), (uint8_t*)&nonce, (uint8_t*)&nonce + 4);
            std::string header_hex = bytes_to_hex(header80.data(), 80);

            // Display hash for logging
            std::vector<uint8_t> hash_bin(32);
            hasher.sha256d(header80.data(), 80, hash_bin.data());
            std::vector<uint8_t> hash_disp = hash_bin;
            std::reverse(hash_disp.begin(), hash_disp.end());
            std::string hash_hex = bytes_to_hex(hash_disp.data(), 32);

            double total_elapsed = std::chrono::duration<double>(
                std::chrono::high_resolution_clock::now() - t_start).count();

            std::cout << "\n" << C_GREEN << "[share]" << C_RESET
                      << " nonce " << C_YELLOW << nonce << C_RESET
                      << " hash 0x" << C_DIM << hash_hex.substr(0, 16) << "..." << C_RESET
                      << " (" << std::fixed << std::setprecision(2) << total_elapsed << "s)"
                      << std::endl;

            // Submit share to pool
            std::string submit_json = "{\"worker_addr\":\"" + address + "\","
                                     "\"job_id\":\"" + job_id + "\","
                                     "\"header_hex\":\"" + header_hex + "\"}";
            HTTPResult sub = http_request(host, port, "/pool/submit", "POST",
                                          submit_json, token, https);
            if (sub.status == 200) {
                accepted_shares++;
                // Parse new pool_diff from response for vardiff
                bool is_block = (sub.body.find("\"is_block\":true") != std::string::npos);
                std::cout << C_GREEN << "[pool ]" << C_RESET
                          << " " << C_GREEN << (is_block ? "BLOCK FOUND!" : "share accepted") << C_RESET
                          << " (" << accepted_shares << " shares)"
                          << std::endl;
            } else {
                std::cout << C_RED << "[pool ]" << C_RESET
                          << " " << C_RED << "share rejected" << C_RESET
                          << ": " << sub.body << std::endl;
            }
            continue;  // next round
        }

        // ── SOLO MODE (original code) ─────────────────────────────────────────
        std::string template_path = "/mining/template?address=" + url_encode(address);
        HTTPResult res = http_request(host, port, template_path, "GET", "", token, https);
        if (res.status != 200) {
            std::cout << "[ERROR] Cannot fetch mining template (HTTP " << res.status
                      << ", WinHTTP " << res.error << "): " << res.body
                      << ". Retrying in 3s..." << std::endl;
            std::this_thread::sleep_for(std::chrono::seconds(3));
            continue;
        }

        std::string json_tpl = res.body;
        int64_t height = json_extract_int64(json_tpl, "height");
        int64_t reward = json_extract_int64(json_tpl, "reward_sats");
        uint32_t bits = (uint32_t)json_extract_int64(json_tpl, "bits");
        uint32_t timestamp = (uint32_t)json_extract_int64(json_tpl, "timestamp");
        std::string prev_hash_hex = json_extract_string(json_tpl, "prev_hash");
        std::string target_hex = json_extract_string(json_tpl, "target");

        if (target_hex.rfind("0x", 0) == 0) target_hex = target_hex.substr(2);
        while (target_hex.length() < 64) target_hex = "0" + target_hex;

        std::vector<uint8_t> target_bytes = hex_to_bytes(target_hex);
        std::vector<uint8_t> coinbase_raw = build_coinbase(height, reward, address);

        std::vector<uint8_t> cb_txid(32);
        hasher.sha256d(coinbase_raw.data(), coinbase_raw.size(), cb_txid.data());

        std::vector<std::vector<uint8_t>> txids;
        txids.push_back(cb_txid);

        std::vector<std::string> mempool_txs = json_extract_string_array(json_tpl, "txs");
        std::vector<std::vector<uint8_t>> raw_tx_list;
        raw_tx_list.push_back(coinbase_raw);

        for (const auto& tx_hex : mempool_txs) {
            std::vector<uint8_t> tx_raw = hex_to_bytes(tx_hex);
            raw_tx_list.push_back(tx_raw);
            std::vector<uint8_t> tid(32);
            hasher.sha256d(tx_raw.data(), tx_raw.size(), tid.data());
            txids.push_back(tid);
        }

        std::vector<uint8_t> merkle_root = compute_merkle_root(hasher, txids);
        std::vector<uint8_t> prev_hash = hex_to_bytes(prev_hash_hex);
        // ORI `hexstr()` displays hashes reversed; block headers store internal bytes.
        // This matches miner.py: unhexstr(template["prev_hash"]).
        if (prev_hash.size() != 32) {
            std::cout << "[ERROR] Invalid prev_hash in template. Refreshing..." << std::endl;
            continue;
        }
        std::reverse(prev_hash.begin(), prev_hash.end());

        std::vector<uint8_t> static76;
        int32_t version = 1;
        static76.insert(static76.end(), (uint8_t*)&version, (uint8_t*)&version + 4);
        static76.insert(static76.end(), prev_hash.begin(), prev_hash.end());
        static76.insert(static76.end(), merkle_root.begin(), merkle_root.end());
        static76.insert(static76.end(), (uint8_t*)&timestamp, (uint8_t*)&timestamp + 4);
        static76.insert(static76.end(), (uint8_t*)&bits, (uint8_t*)&bits + 4);

        double diff = target_to_difficulty(target_bytes);
        std::cout << C_BLUE << "[net ]" << C_RESET
                  << " new job from " << host << ":" << port
                  << " diff " << C_YELLOW << std::fixed << std::setprecision(2) << diff << C_RESET
                  << " height " << C_CYAN << height << C_RESET
                  << " txs " << raw_tx_list.size()
                  << " target 0x" << C_DIM << target_hex.substr(0, 16) << "..." << C_RESET
                  << std::endl;

        // Per-round isolated state — no globals, no stale data from prev round.
        RoundState rs;

        std::vector<std::thread> workers;
        workers.reserve(threads);
        auto t_start = std::chrono::high_resolution_clock::now();

        for (int i = 0; i < threads; ++i) {
            workers.emplace_back(pow_worker, static76, target_bytes,
                                 (uint32_t)i, (uint32_t)threads, &rs);
        }

        // Monitor: print hashrate every second; refresh template every 30s.
        for (int sec = 0; sec < 30; ++sec) {
            std::this_thread::sleep_for(std::chrono::seconds(1));
            if (rs.found.load(std::memory_order_acquire)) break;
            if (g_stop_mining.load(std::memory_order_relaxed)) {
                rs.stop.store(true);
                break;
            }
            auto t_now = std::chrono::high_resolution_clock::now();
            double elapsed = std::chrono::duration<double>(t_now - t_start).count();
            double mh = (rs.total_hashes.load(std::memory_order_relaxed) / 1000000.0)
                        / (elapsed > 0 ? elapsed : 1.0);
            std::cout << "\r" << C_CYAN << "[cpu ]" << C_RESET
                      << " threads " << C_CYAN << threads << C_RESET
                      << " | " << C_YELLOW << std::fixed << std::setprecision(2) << mh << " MH/s" << C_RESET
                      << " | " << C_DIM << (rs.total_hashes.load(std::memory_order_relaxed) / 1000000.0)
                      << " Mhashes " << std::setprecision(0) << elapsed << "s" << C_RESET
                      << "    " << std::flush;
        }

        // Signal workers to stop this round and wait for all to finish.
        rs.stop.store(true, std::memory_order_relaxed);
        for (auto& w : workers) {
            if (w.joinable()) w.join();
        }

        auto t_end = std::chrono::high_resolution_clock::now();
        double total_elapsed = std::chrono::duration<double>(t_end - t_start).count();

        if (rs.found.load(std::memory_order_acquire)) {
            uint32_t nonce = rs.winning_nonce.load(std::memory_order_relaxed);

            std::vector<uint8_t> header80 = static76;
            header80.insert(header80.end(), (uint8_t*)&nonce, (uint8_t*)&nonce + 4);

            std::vector<uint8_t> block_hash_bin(32);
            hasher.sha256d(header80.data(), header80.size(), block_hash_bin.data());
            // ORI display hex reversed
            std::vector<uint8_t> block_hash_disp = block_hash_bin;
            std::reverse(block_hash_disp.begin(), block_hash_disp.end());
            std::string block_hash_hex = bytes_to_hex(block_hash_disp.data(), block_hash_disp.size());

            std::vector<uint8_t> block_bin = header80;
            std::vector<uint8_t> ntx_var = varint_encode(raw_tx_list.size());
            block_bin.insert(block_bin.end(), ntx_var.begin(), ntx_var.end());

            for (const auto& tx_raw : raw_tx_list) {
                block_bin.insert(block_bin.end(), tx_raw.begin(), tx_raw.end());
            }

            std::string block_hex = bytes_to_hex(block_bin.data(), block_bin.size());
            std::string post_json = "{\"block\":\"" + block_hex + "\"}";

            std::cout << "\n" << C_GREEN << "[block]" << C_RESET
                      << " solution found at height " << C_CYAN << height << C_RESET
                      << " nonce " << C_YELLOW << nonce << C_RESET
                      << " time " << C_DIM << std::fixed << std::setprecision(2) << total_elapsed << "s" << C_RESET
                      << " hash " << C_DIM << block_hash_hex << C_RESET
                      << std::endl;

            HTTPResult sub_res = http_request(host, port, "/mining/submit", "POST", post_json, token, https);
            if (sub_res.status == 200) {
                accepted_blocks++;
                std::cout << C_GREEN << "[net ]" << C_RESET
                          << " " << C_GREEN << "accepted" << C_RESET
                          << " (" << accepted_blocks << "/0) diff "
                          << std::fixed << std::setprecision(2) << diff
                          << " (" << (int)(total_elapsed * 1000) << " ms)"
                          << std::endl;
            } else {
                std::cout << C_RED << "[net ]" << C_RESET
                          << " " << C_RED << "rejected" << C_RESET
                          << " by node: " << sub_res.body
                          << std::endl;
            }
        }
    }

    return 0;
}
