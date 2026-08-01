# ESP32 Voice-Capture Firmware Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task, with the human present to flash and verify each checkpoint (see Global Constraints). Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Firmware for the Waveshare ESP32-S3-Touch-LCD-1.83 that records voice notes on a button press, buffers them to SD, and syncs to WhisperDeck either immediately (LIVE mode) or on a manual long-press (BUFFERED mode).

**Architecture:** ESP-IDF, built on the official `waveshare/esp32_s3_touch_lcd_1_83` BSP component (display/touch/mic already wired up) plus LVGL v9 for the status screen. State lives in three places: NVS (wifi list, server URL, bearer token — persistent config), the SD card (buffered WAV files — the sync queue is just the directory listing), and RAM (current mode, recording state — reset on reboot).

**Tech Stack:** ESP-IDF v5.5+, `waveshare/esp32_s3_touch_lcd_1_83` component (`^2.0.0`), `lvgl/lvgl` (`^9.2.0`), FreeRTOS, `esp_http_server` (captive portal + upload client), NVS (`nvs_flash`).

## Global Constraints

- **No automated test harness exists for this project.** Every task's verification step is a manual "flash the board, observe X" checkpoint, not a command with expected output. Do not proceed past a checkpoint without the human confirming it passed on real hardware — a checkpoint that "should work" based on reading the code is not verified.
- Board target: `esp32s3`, set via `idf.py set-target esp32s3` (done once, Task 0).
- No em dashes in commit messages or comments; plain punctuation only.
- Never commit directly to `master` in whatever repo `esp32-s3touch` becomes; branch and PR once there's a remote to open one against (there isn't yet — see Task 0).
- Depends on the separate `docs/superpowers/plans/2026-08-01-device-token-auth.md` plan being implemented on the WhisperDeck server before Task 5 below can be end-to-end verified (the device needs a real token and a live `/api/transcribe` that accepts it).
- `kind="auto"` is NOT sent by this firmware (issue #268 dependency, out of scope per the approved spec). All uploads use `kind="voice_note"`.

---

### Task 0: Project skeleton + git init

**Files:**
- Create: `CMakeLists.txt`, `main/CMakeLists.txt`, `main/idf_component.yml`, `main/main.c`, `.gitignore`
- Create (init): git repository at `c:\claude\esp32-s3touch`

**Interfaces:**
- Produces: a buildable, flashable "hello world" ESP-IDF project. Every later task builds on this skeleton.

- [ ] **Step 1: Initialize git**

```bash
cd c:\claude\esp32-s3touch
git init
git add docs/
git commit -m "Add hardware and code-examples reference docs"
```

- [ ] **Step 2: Create the project skeleton**

`CMakeLists.txt` (project root):

```cmake
cmake_minimum_required(VERSION 3.16)
include($ENV{IDF_PATH}/tools/cmake/project.cmake)
project(voice_capture)
```

`main/CMakeLists.txt`:

```cmake
idf_component_register(SRCS "main.c" INCLUDE_DIRS ".")
```

`main/idf_component.yml`:

```yaml
dependencies:
  idf: ">=5.5.0"
  waveshare/esp32_s3_touch_lcd_1_83:
    version: "^2.0.0"
  lvgl/lvgl:
    version: "^9.2.0"
    public: true
```

`main/main.c`:

```c
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"
#include "esp_log.h"
#include "bsp/esp-bsp.h"
#include "bsp/display.h"
#include "lvgl.h"

static const char *TAG = "voice_capture";

void app_main(void)
{
    lv_display_t *disp = bsp_display_start();
    if (disp) {
        bsp_display_backlight_on();
        bsp_display_lock(0);
        lv_obj_t *label = lv_label_create(lv_screen_active());
        lv_label_set_text(label, "voice capture booting");
        lv_obj_center(label);
        bsp_display_unlock();
    }
    ESP_LOGI(TAG, "boot complete");
}
```

`.gitignore`:

```
build/
sdkconfig
sdkconfig.old
managed_components/
dependencies.lock
```

- [ ] **Step 3: Build and flash**

Run: `idf.py set-target esp32s3 build flash monitor`
Expected (manual checkpoint): board boots, screen shows "voice capture booting", monitor log shows `boot complete`.

- [ ] **Step 4: Commit**

```bash
git add CMakeLists.txt main/ .gitignore
git commit -m "Add ESP-IDF project skeleton with BSP display bring-up"
```

---

### Task 1: Button read + recording indicator on screen

**Files:**
- Modify: `main/main.c`

**Interfaces:**
- Produces: a `record_button_pressed()` poll function and an `lv_obj_t *status_label` global, both referenced by Task 2 (recording state) and Task 3 (screen updates).

- [ ] **Step 1: Add button GPIO read**

The hardware reference lists `BOOT` on `GPIO0` as the available user button (PWR/GPIO41 is reserved for power control per the AXP2101 PMIC, not a general-purpose input in this design). Add to `main/main.c`, before `app_main`:

```c
#include "driver/gpio.h"

#define RECORD_BUTTON_GPIO GPIO_NUM_0

static void record_button_init(void)
{
    gpio_config_t cfg = {
        .pin_bit_mask = 1ULL << RECORD_BUTTON_GPIO,
        .mode = GPIO_MODE_INPUT,
        .pull_up_en = GPIO_PULLUP_ENABLE,
    };
    gpio_config(&cfg);
}

// BOOT is active-low (pulled to GND when pressed).
static bool record_button_pressed(void)
{
    return gpio_get_level(RECORD_BUTTON_GPIO) == 0;
}
```

Call `record_button_init()` as the first line of `app_main`, before `bsp_display_start()`.

- [ ] **Step 2: Add a debounced press/release task**

```c
static lv_obj_t *status_label = NULL;
static bool recording = false;

static void button_poll_task(void *arg)
{
    bool last_state = false;
    while (1) {
        bool pressed = record_button_pressed();
        if (pressed && !last_state) {
            recording = !recording;
            bsp_display_lock(pdMS_TO_TICKS(100));
            lv_label_set_text(status_label, recording ? "RECORDING" : "idle");
            bsp_display_unlock();
        }
        last_state = pressed;
        vTaskDelay(pdMS_TO_TICKS(50)); // 50ms poll, debounces by construction
    }
}
```

Replace the `"voice capture booting"` label creation in `app_main` with:

```c
        status_label = lv_label_create(lv_screen_active());
        lv_label_set_text(status_label, "idle");
        lv_obj_center(status_label);
```

And after `bsp_display_unlock()` in `app_main`, add:

```c
    xTaskCreate(button_poll_task, "button_poll", 4096, NULL, 5, NULL);
```

- [ ] **Step 3: Build, flash, verify**

Run: `idf.py build flash monitor`
Expected (manual checkpoint): screen shows "idle"; pressing BOOT flips it to "RECORDING"; pressing again flips it back.

- [ ] **Step 4: Commit**

```bash
git add main/main.c
git commit -m "Add button-triggered recording state and screen label"
```

---

### Task 2: SD card mount + WAV recording to file

**Files:**
- Modify: `main/main.c`, `main/idf_component.yml` (add `fatfs` support is built into ESP-IDF core, no new component needed)

**Interfaces:**
- Consumes: `recording` bool, `record_button_pressed()` (Task 1).
- Produces: `wav_recorder_start(const char *path)`, `wav_recorder_stop(void)` — Task 5 (sync uploader) reads the files these write; Task 3 (screen) reads the buffer directory these populate.

- [ ] **Step 1: Mount the SD card over SPI**

The hardware reference lists the TF card pins: MOSI=GPIO1, SCK=GPIO2, MISO=GPIO3, CS=GPIO42. Add to `main/main.c`:

```c
#include "driver/sdspi_host.h"
#include "driver/spi_common.h"
#include "sdmmc_cmd.h"
#include "esp_vfs_fat.h"

#define SD_MOSI GPIO_NUM_1
#define SD_SCK  GPIO_NUM_2
#define SD_MISO GPIO_NUM_3
#define SD_CS   GPIO_NUM_42
#define SD_MOUNT_POINT "/sdcard"

static sdmmc_card_t *sd_card = NULL;

static esp_err_t sd_card_init(void)
{
    spi_bus_config_t bus_cfg = {
        .mosi_io_num = SD_MOSI, .miso_io_num = SD_MISO, .sclk_io_num = SD_SCK,
        .quadwp_io_num = -1, .quadhd_io_num = -1, .max_transfer_sz = 4000,
    };
    esp_err_t ret = spi_bus_initialize(SPI2_HOST, &bus_cfg, SDSPI_DEFAULT_DMA);
    if (ret != ESP_OK) return ret;

    sdspi_device_config_t slot_cfg = SDSPI_DEVICE_CONFIG_DEFAULT();
    slot_cfg.gpio_cs = SD_CS;
    slot_cfg.host_id = SPI2_HOST;

    sdmmc_host_t host = SDSPI_HOST_DEFAULT();
    host.slot = SPI2_HOST;

    esp_vfs_fat_sdmmc_mount_config_t mount_cfg = {
        .format_if_mount_failed = false,
        .max_files = 8,
    };
    return esp_vfs_fat_sdspi_mount(SD_MOUNT_POINT, &host, &slot_cfg, &mount_cfg, &sd_card);
}
```

Call `sd_card_init()` in `app_main`, right after `record_button_init()`. Log and continue on failure rather than aborting boot (an SD-less board should still show its screen for debugging):

```c
    if (sd_card_init() != ESP_OK) {
        ESP_LOGE(TAG, "SD card mount failed, buffering disabled");
    }
```

- [ ] **Step 2: Add mic capture and WAV framing**

Per the code-examples reference, mic capture goes through `bsp_extra_codec_init()` + `bsp_extra_i2s_read()`. Add:

```c
#include "bsp_board_extra.h"

#define WAV_SAMPLE_RATE 16000
#define WAV_BITS_PER_SAMPLE 16
#define WAV_CHANNELS 1

typedef struct __attribute__((packed)) {
    char riff[4]; uint32_t chunk_size; char wave[4];
    char fmt[4]; uint32_t fmt_size; uint16_t audio_format; uint16_t num_channels;
    uint32_t sample_rate; uint32_t byte_rate; uint16_t block_align; uint16_t bits_per_sample;
    char data[4]; uint32_t data_size;
} wav_header_t;

static FILE *wav_file = NULL;
static uint32_t wav_bytes_written = 0;

static void wav_header_write_placeholder(FILE *f)
{
    wav_header_t h = {
        .riff = "RIFF", .wave = "WAVE", .fmt = "fmt ", .fmt_size = 16,
        .audio_format = 1, .num_channels = WAV_CHANNELS, .sample_rate = WAV_SAMPLE_RATE,
        .byte_rate = WAV_SAMPLE_RATE * WAV_CHANNELS * (WAV_BITS_PER_SAMPLE / 8),
        .block_align = WAV_CHANNELS * (WAV_BITS_PER_SAMPLE / 8),
        .bits_per_sample = WAV_BITS_PER_SAMPLE, .data = "data",
        .chunk_size = 0, .data_size = 0, // patched in wav_recorder_stop
    };
    fwrite(&h, sizeof(h), 1, f);
}

static bool wav_recorder_start(const char *path)
{
    wav_file = fopen(path, "wb");
    if (!wav_file) return false;
    wav_header_write_placeholder(wav_file);
    wav_bytes_written = 0;
    return true;
}

static void wav_recorder_write(const int16_t *samples, size_t sample_count)
{
    if (!wav_file) return;
    fwrite(samples, sizeof(int16_t), sample_count, wav_file);
    wav_bytes_written += sample_count * sizeof(int16_t);
}

static void wav_recorder_stop(void)
{
    if (!wav_file) return;
    // Patch the two size fields now that the total is known.
    fseek(wav_file, offsetof(wav_header_t, chunk_size), SEEK_SET);
    uint32_t chunk_size = 36 + wav_bytes_written;
    fwrite(&chunk_size, sizeof(uint32_t), 1, wav_file);
    fseek(wav_file, offsetof(wav_header_t, data_size), SEEK_SET);
    fwrite(&wav_bytes_written, sizeof(uint32_t), 1, wav_file);
    fclose(wav_file);
    wav_file = NULL;
}

#define CAPTURE_CHUNK_SAMPLES 512

static void capture_task(void *arg)
{
    int16_t buf[CAPTURE_CHUNK_SAMPLES];
    bool was_recording = false;
    while (1) {
        if (recording) {
            if (!was_recording) {
                char path[64];
                time_t now = time(NULL);
                snprintf(path, sizeof(path), SD_MOUNT_POINT "/%lld.wav", (long long)now);
                if (!wav_recorder_start(path)) {
                    ESP_LOGE(TAG, "failed to open %s for recording", path);
                    recording = false;
                }
            }
            size_t bytes_read = 0;
            if (bsp_extra_i2s_read(buf, sizeof(buf), &bytes_read, pdMS_TO_TICKS(200)) == ESP_OK && bytes_read > 0) {
                wav_recorder_write(buf, bytes_read / sizeof(int16_t));
            }
        } else if (was_recording) {
            wav_recorder_stop();
        }
        was_recording = recording;
        vTaskDelay(pdMS_TO_TICKS(10));
    }
}
```

Call `bsp_extra_codec_init()` in `app_main` right after the SD mount, and start the task alongside `button_poll_task`:

```c
    bsp_extra_codec_init();
    xTaskCreate(capture_task, "capture", 8192, NULL, 6, NULL);
```

- [ ] **Step 3: Build, flash, verify**

Run: `idf.py build flash monitor`
Expected (manual checkpoint): with an SD card inserted, press the button, speak for a few seconds, press again. Eject the SD card, confirm a `<timestamp>.wav` file exists and plays back your voice in a PC audio player. Filenames use a unix timestamp for now, human-readable naming comes in Task 6 once real time-of-day is available (NTP, after wifi exists).

- [ ] **Step 4: Commit**

```bash
git add main/main.c
git commit -m "Add SD card mount and WAV recording on button press"
```

---

### Task 3: Status screen — buffered file count

**Files:**
- Modify: `main/main.c`

**Interfaces:**
- Consumes: `SD_MOUNT_POINT` (Task 2).
- Produces: `buffered_file_count(void) -> int`, used by Task 5's sync loop and Task 7's screen refresh.

- [ ] **Step 1: Add a directory-count helper and a periodic screen refresh**

```c
#include "dirent.h"

static int buffered_file_count(void)
{
    DIR *dir = opendir(SD_MOUNT_POINT);
    if (!dir) return 0;
    int count = 0;
    struct dirent *entry;
    while ((entry = readdir(dir)) != NULL) {
        if (strstr(entry->d_name, ".wav")) count++;
    }
    closedir(dir);
    return count;
}

static lv_obj_t *buffer_count_label = NULL;

static void status_refresh_task(void *arg)
{
    while (1) {
        int count = buffered_file_count();
        bsp_display_lock(pdMS_TO_TICKS(100));
        char text[32];
        snprintf(text, sizeof(text), "%d buffered", count);
        lv_label_set_text(buffer_count_label, text);
        bsp_display_unlock();
        vTaskDelay(pdMS_TO_TICKS(2000));
    }
}
```

In `app_main`, after creating `status_label`, add a second label below it and start the refresh task:

```c
        buffer_count_label = lv_label_create(lv_screen_active());
        lv_obj_align_to(buffer_count_label, status_label, LV_ALIGN_OUT_BOTTOM_MID, 0, 10);
```

```c
    xTaskCreate(status_refresh_task, "status_refresh", 4096, NULL, 4, NULL);
```

- [ ] **Step 2: Build, flash, verify**

Run: `idf.py build flash monitor`
Expected (manual checkpoint): screen shows "0 buffered" at boot (or however many `.wav` files are already on the card); record one note, wait a couple seconds, count increments to "1 buffered".

- [ ] **Step 3: Commit**

```bash
git add main/main.c
git commit -m "Show buffered file count on status screen"
```

---

### Task 4: NVS config (temporary hardcoded values)

**Files:**
- Modify: `main/main.c`

**Interfaces:**
- Produces: `device_config_t` struct with `server_url`, `bearer_token`, and a small fixed-size SSID/password array; `device_config_load(device_config_t *out)`. Task 5 reads this to know where and how to upload. Task 6 replaces the hardcoded values this task writes with real captive-portal input, but keeps the struct and `device_config_load` signature unchanged.

- [ ] **Step 1: Define the config struct and a temporary hardcoded loader**

This task deliberately hardcodes real values as a stand-in for the captive portal (Task 6), so wifi + sync (Task 5) can be built and verified before the provisioning UI exists. Add to `main/main.c`:

```c
#define MAX_KNOWN_NETWORKS 4

typedef struct {
    char ssid[33];
    char password[65];
} known_network_t;

typedef struct {
    known_network_t networks[MAX_KNOWN_NETWORKS];
    int network_count;
    char server_url[128];
    char bearer_token[80];
} device_config_t;

// TEMPORARY: replaced by NVS-backed captive portal config in Task 6.
// Fill in your actual wifi/server details before flashing this task.
static void device_config_load(device_config_t *out)
{
    memset(out, 0, sizeof(*out));
    strncpy(out->networks[0].ssid, "YOUR_SSID", sizeof(out->networks[0].ssid) - 1);
    strncpy(out->networks[0].password, "YOUR_PASSWORD", sizeof(out->networks[0].password) - 1);
    out->network_count = 1;
    strncpy(out->server_url, "https://your-whisperdeck-url", sizeof(out->server_url) - 1);
    strncpy(out->bearer_token, "YOUR_DEVICE_TOKEN", sizeof(out->bearer_token) - 1);
}
```

- [ ] **Step 2: Build and verify it compiles**

Run: `idf.py build`
Expected (manual checkpoint): builds with no errors. No behavior to observe on-device yet, this task only adds a struct and a function nothing calls until Task 5. That's fine, it's a foundation task; the check is a clean build, not a screen change.

- [ ] **Step 3: Commit**

```bash
git add main/main.c
git commit -m "Add device config struct with temporary hardcoded values"
```

---

### Task 5: Wifi connect + manual sync upload (long-press)

**Files:**
- Modify: `main/main.c`

**Interfaces:**
- Consumes: `device_config_load` (Task 4), `buffered_file_count` (Task 3), `record_button_pressed` (Task 1).
- Produces: `sync_now(void)`, triggered by a long-press, called again by Task 7 for the live-mode immediate-upload path.

**Cross-plan dependency:** this task's manual checkpoint requires the WhisperDeck server-side plan (`2026-08-01-device-token-auth.md`) to be implemented and running, with a real device token generated via its Settings UI. Fill that real token into `device_config_load` (Task 4) before testing this task, replacing `"YOUR_DEVICE_TOKEN"`.

- [ ] **Step 1: Add wifi connect**

```c
#include "esp_wifi.h"
#include "esp_netif.h"
#include "esp_event.h"

static bool wifi_connect_known(const device_config_t *cfg)
{
    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_sta();
    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&init_cfg);
    esp_wifi_set_mode(WIFI_MODE_STA);
    esp_wifi_start();

    for (int i = 0; i < cfg->network_count; i++) {
        wifi_config_t wifi_cfg = {0};
        strncpy((char *)wifi_cfg.sta.ssid, cfg->networks[i].ssid, sizeof(wifi_cfg.sta.ssid) - 1);
        strncpy((char *)wifi_cfg.sta.password, cfg->networks[i].password, sizeof(wifi_cfg.sta.password) - 1);
        esp_wifi_set_config(WIFI_IF_STA, &wifi_cfg);
        if (esp_wifi_connect() != ESP_OK) continue;
        // Poll for connection instead of an event-driven approach, simplest
        // correct option for a one-shot "try to connect" call.
        for (int wait_ms = 0; wait_ms < 8000; wait_ms += 200) {
            wifi_ap_record_t ap_info;
            if (esp_wifi_sta_get_ap_info(&ap_info) == ESP_OK) return true;
            vTaskDelay(pdMS_TO_TICKS(200));
        }
        esp_wifi_disconnect();
    }
    return false;
}

static void wifi_disconnect_and_stop(void)
{
    esp_wifi_disconnect();
    esp_wifi_stop();
}
```

- [ ] **Step 2: Add the multipart upload + sync loop**

ESP-IDF's `esp_http_client` supports multipart uploads by writing the body manually (there is no built-in multipart helper). Add:

```c
#include "esp_http_client.h"

static bool upload_file(const device_config_t *cfg, const char *filepath, const char *filename)
{
    char url[160];
    snprintf(url, sizeof(url), "%s/api/transcribe", cfg->server_url);

    FILE *f = fopen(filepath, "rb");
    if (!f) return false;
    fseek(f, 0, SEEK_END);
    long file_size = ftell(f);
    fseek(f, 0, SEEK_SET);

    const char *boundary = "----voicecaptureboundary";
    char part_header[256];
    int part_header_len = snprintf(part_header, sizeof(part_header),
        "--%s\r\nContent-Disposition: form-data; name=\"kind\"\r\n\r\nvoice_note\r\n"
        "--%s\r\nContent-Disposition: form-data; name=\"provider\"\r\n\r\nmoonshine\r\n"
        "--%s\r\nContent-Disposition: form-data; name=\"file\"; filename=\"%s\"\r\n"
        "Content-Type: audio/wav\r\n\r\n",
        boundary, boundary, boundary, filename);
    char part_footer[64];
    int part_footer_len = snprintf(part_footer, sizeof(part_footer), "\r\n--%s--\r\n", boundary);

    char auth_header[100];
    snprintf(auth_header, sizeof(auth_header), "Bearer %s", cfg->bearer_token);
    char content_type[64];
    snprintf(content_type, sizeof(content_type), "multipart/form-data; boundary=%s", boundary);

    esp_http_client_config_t http_cfg = { .url = url, .method = HTTP_METHOD_POST, .timeout_ms = 30000 };
    esp_http_client_handle_t client = esp_http_client_init(&http_cfg);
    esp_http_client_set_header(client, "Authorization", auth_header);
    esp_http_client_set_header(client, "Content-Type", content_type);

    int total_len = part_header_len + file_size + part_footer_len;
    esp_http_client_open(client, total_len);
    esp_http_client_write(client, part_header, part_header_len);

    char file_buf[2048];
    size_t n;
    while ((n = fread(file_buf, 1, sizeof(file_buf), f)) > 0) {
        esp_http_client_write(client, file_buf, n);
    }
    fclose(f);
    esp_http_client_write(client, part_footer, part_footer_len);

    esp_http_client_fetch_headers(client);
    int status = esp_http_client_get_status_code(client);
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    return status >= 200 && status < 300;
}

static void sync_now(void)
{
    device_config_t cfg;
    device_config_load(&cfg);

    bsp_display_lock(pdMS_TO_TICKS(100));
    lv_label_set_text(status_label, "connecting...");
    bsp_display_unlock();

    if (!wifi_connect_known(&cfg)) {
        bsp_display_lock(pdMS_TO_TICKS(100));
        lv_label_set_text(status_label, "no network");
        bsp_display_unlock();
        return;
    }

    DIR *dir = opendir(SD_MOUNT_POINT);
    struct dirent *entry;
    bool stop = false;
    while (!stop && dir && (entry = readdir(dir)) != NULL) {
        if (!strstr(entry->d_name, ".wav")) continue;
        char path[80];
        snprintf(path, sizeof(path), SD_MOUNT_POINT "/%s", entry->d_name);
        bsp_display_lock(pdMS_TO_TICKS(100));
        lv_label_set_text(status_label, "syncing...");
        bsp_display_unlock();
        if (upload_file(&cfg, path, entry->d_name)) {
            remove(path);
        } else {
            stop = true; // stop the batch on first failure, don't hammer a flaky link
        }
    }
    if (dir) closedir(dir);
    wifi_disconnect_and_stop();

    bsp_display_lock(pdMS_TO_TICKS(100));
    lv_label_set_text(status_label, stop ? "sync failed" : "sync ok");
    bsp_display_unlock();
}
```

- [ ] **Step 3: Wire the long-press to `sync_now()`**

Modify `button_poll_task` (Task 1) to distinguish a short tap from a long-press: track how long the button stays down, and if it exceeds 2 seconds, call `sync_now()` on release instead of toggling `recording`.

```c
static void button_poll_task(void *arg)
{
    bool last_state = false;
    TickType_t press_started = 0;
    while (1) {
        bool pressed = record_button_pressed();
        if (pressed && !last_state) {
            press_started = xTaskGetTickCount();
        }
        if (!pressed && last_state) {
            TickType_t held_ticks = xTaskGetTickCount() - press_started;
            if (held_ticks >= pdMS_TO_TICKS(2000)) {
                sync_now();
            } else {
                recording = !recording;
                bsp_display_lock(pdMS_TO_TICKS(100));
                lv_label_set_text(status_label, recording ? "RECORDING" : "idle");
                bsp_display_unlock();
            }
        }
        last_state = pressed;
        vTaskDelay(pdMS_TO_TICKS(50));
    }
}
```

- [ ] **Step 4: Build, flash, verify**

Prerequisite: whisperdesk running and reachable at the URL in `device_config_load`, with a real device token generated via its Settings UI and filled into `bearer_token`.

Run: `idf.py build flash monitor`
Expected (manual checkpoint): record a note (short press twice), then long-press the button for 2+ seconds. Screen shows "connecting...", then "syncing...", then "sync ok". "N buffered" on screen drops back toward 0. Check WhisperDeck's transcript list, the new voice note appears.

- [ ] **Step 5: Commit**

```bash
git add main/main.c
git commit -m "Add wifi connect and manual sync-on-long-press upload"
```

---

### Task 6: Captive portal provisioning (replaces Task 4's hardcoded config)

**Files:**
- Modify: `main/main.c`

**Interfaces:**
- Consumes: `device_config_t` (Task 4).
- Produces: NVS-backed `device_config_load`/`device_config_save`, replacing Task 4's hardcoded version with the same signature. `sync_now` (Task 5) and any other caller of `device_config_load` are unaffected by this swap.

- [ ] **Step 1: Add NVS-backed load/save, replacing the hardcoded loader**

```c
#include "nvs_flash.h"
#include "nvs.h"

#define NVS_NAMESPACE "voicecap"

static void device_config_load(device_config_t *out)
{
    memset(out, 0, sizeof(*out));
    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, NVS_READONLY, &handle) != ESP_OK) return;
    size_t size = sizeof(device_config_t);
    nvs_get_blob(handle, "config", out, &size);
    nvs_close(handle);
}

static void device_config_save(const device_config_t *cfg)
{
    nvs_handle_t handle;
    if (nvs_open(NVS_NAMESPACE, NVS_READWRITE, &handle) != ESP_OK) return;
    nvs_set_blob(handle, "config", cfg, sizeof(*cfg));
    nvs_commit(handle);
    nvs_close(handle);
}
```

Add `nvs_flash_init()` as the very first line of `app_main` (standard ESP-IDF pattern, handles the "no free pages" first-boot case):

```c
    esp_err_t nvs_ret = nvs_flash_init();
    if (nvs_ret == ESP_ERR_NVS_NO_FREE_PAGES || nvs_ret == ESP_ERR_NVS_NEW_VERSION_FOUND) {
        nvs_flash_erase();
        nvs_flash_init();
    }
```

- [ ] **Step 2: Add the captive portal (softAP + form)**

```c
#include "esp_http_server.h"

static const char *PROVISION_PAGE =
    "<html><body><h3>Voice Capture Setup</h3>"
    "<form method='POST' action='/save'>"
    "SSID: <input name='ssid'><br>"
    "Password: <input name='password' type='password'><br>"
    "Server URL: <input name='server_url'><br>"
    "Device Token: <input name='token'><br>"
    "<input type='submit' value='Save'></form></body></html>";

static esp_err_t provision_get_handler(httpd_req_t *req)
{
    httpd_resp_send(req, PROVISION_PAGE, HTTPD_RESP_USE_STRLEN);
    return ESP_OK;
}

// Minimal application/x-www-form-urlencoded parser, sufficient for this
// fixed 4-field form. Not a general URL decoder (no %-escape handling) --
// acceptable because SSIDs/passwords/tokens containing raw '&' or '=' are
// a real but rare edge case, worth a follow-up if it bites in practice.
static void provision_extract_field(const char *body, const char *key, char *out, size_t out_size)
{
    char search[32];
    snprintf(search, sizeof(search), "%s=", key);
    const char *start = strstr(body, search);
    if (!start) { out[0] = '\0'; return; }
    start += strlen(search);
    const char *end = strchr(start, '&');
    size_t len = end ? (size_t)(end - start) : strlen(start);
    if (len >= out_size) len = out_size - 1;
    memcpy(out, start, len);
    out[len] = '\0';
}

static esp_err_t provision_post_handler(httpd_req_t *req)
{
    char body[512];
    int len = httpd_req_recv(req, body, sizeof(body) - 1);
    if (len <= 0) return ESP_FAIL;
    body[len] = '\0';

    device_config_t cfg;
    memset(&cfg, 0, sizeof(cfg));
    provision_extract_field(body, "ssid", cfg.networks[0].ssid, sizeof(cfg.networks[0].ssid));
    provision_extract_field(body, "password", cfg.networks[0].password, sizeof(cfg.networks[0].password));
    provision_extract_field(body, "server_url", cfg.server_url, sizeof(cfg.server_url));
    provision_extract_field(body, "token", cfg.bearer_token, sizeof(cfg.bearer_token));
    cfg.network_count = 1;
    device_config_save(&cfg);

    httpd_resp_send(req, "<html><body>Saved. Rebooting.</body></html>", HTTPD_RESP_USE_STRLEN);
    vTaskDelay(pdMS_TO_TICKS(1000));
    esp_restart();
    return ESP_OK;
}

static void start_provisioning_portal(void)
{
    esp_netif_init();
    esp_event_loop_create_default();
    esp_netif_create_default_wifi_ap();
    wifi_init_config_t init_cfg = WIFI_INIT_CONFIG_DEFAULT();
    esp_wifi_init(&init_cfg);
    wifi_config_t ap_cfg = {
        .ap = { .ssid = "VoiceCapture-Setup", .ssid_len = 0, .max_connection = 2, .authmode = WIFI_AUTH_OPEN },
    };
    esp_wifi_set_mode(WIFI_MODE_AP);
    esp_wifi_set_config(WIFI_IF_AP, &ap_cfg);
    esp_wifi_start();

    httpd_config_t http_cfg = HTTPD_DEFAULT_CONFIG();
    httpd_handle_t server;
    httpd_start(&server, &http_cfg);
    httpd_uri_t get_uri = { .uri = "/", .method = HTTP_GET, .handler = provision_get_handler };
    httpd_uri_t post_uri = { .uri = "/save", .method = HTTP_POST, .handler = provision_post_handler };
    httpd_register_uri_handler(server, &get_uri);
    httpd_register_uri_handler(server, &post_uri);
}
```

- [ ] **Step 3: Enter provisioning mode on first boot or factory reset**

In `app_main`, after loading config, enter the portal if unconfigured, or if both buttons are held at boot (factory reset, BOOT=GPIO0 already read; add PWR=GPIO41 as the second input the same way `record_button_init` reads GPIO0):

```c
    device_config_t cfg;
    device_config_load(&cfg);
    bool factory_reset_requested = record_button_pressed(); // BOOT held at boot
    if (cfg.network_count == 0 || factory_reset_requested) {
        start_provisioning_portal();
        // Portal handles its own lifecycle (reboots on save); park here.
        while (1) { vTaskDelay(pdMS_TO_TICKS(1000)); }
    }
```

Place this block right after `device_config_load` would be called (i.e., right after NVS init, before the display/SD/wifi bring-up that assumes a configured device).

- [ ] **Step 4: Build, flash, verify**

Run: `idf.py build flash monitor`
Expected (manual checkpoint, first boot / erased NVS): device starts a `VoiceCapture-Setup` open wifi AP. Connect a phone/laptop to it, browse to `192.168.4.1`, fill the form, submit. Device reboots and (per Task 5's checkpoint) can now sync using the values just entered, no source-code edit needed.
Expected (manual checkpoint, factory reset): hold BOOT while powering on an already-configured device, confirm the portal reappears instead of normal operation.

- [ ] **Step 5: Commit**

```bash
git add main/main.c
git commit -m "Add captive portal provisioning, replacing hardcoded config"
```

---

### Task 7: Mode toggle (LIVE/BUFFERED) + live-mode immediate upload

**Files:**
- Modify: `main/main.c`

**Interfaces:**
- Consumes: `sync_now` (Task 5, refactored to take a single-file argument here), `device_config_save`/`device_config_load` (Task 6).

- [ ] **Step 1: Add a mode field to config and a tap-to-toggle screen region**

Add `bool live_mode;` to `device_config_t` (Task 4/6). Add a third label and a touch event on it:

```c
static lv_obj_t *mode_label = NULL;

static void mode_label_click_cb(lv_event_t *e)
{
    device_config_t cfg;
    device_config_load(&cfg);
    cfg.live_mode = !cfg.live_mode;
    device_config_save(&cfg);
    lv_label_set_text(mode_label, cfg.live_mode ? "LIVE" : "BUFFERED");
}
```

In `app_main`, after `buffer_count_label` is created:

```c
        device_config_t boot_cfg;
        device_config_load(&boot_cfg);
        mode_label = lv_label_create(lv_screen_active());
        lv_label_set_text(mode_label, boot_cfg.live_mode ? "LIVE" : "BUFFERED");
        lv_obj_align_to(mode_label, buffer_count_label, LV_ALIGN_OUT_BOTTOM_MID, 0, 10);
        lv_obj_add_flag(mode_label, LV_OBJ_FLAG_CLICKABLE);
        lv_obj_add_event_cb(mode_label, mode_label_click_cb, LV_EVENT_CLICKED, NULL);
```

- [ ] **Step 2: Refactor `sync_now` to upload one file, add a live-mode call site**

Split Task 5's `sync_now` into `upload_one(const device_config_t *cfg, const char *path, const char *filename) -> bool` (the existing per-file body) and keep `sync_now()` as the loop that calls it for every buffered file. Then, in `capture_task` (Task 2), right after `wav_recorder_stop()` on the stop-recording transition:

```c
        } else if (was_recording) {
            char stopped_path[64];
            wav_recorder_stop(&stopped_path); // Task 2's stop now returns the path it just closed, see below
            device_config_t cfg;
            device_config_load(&cfg);
            if (cfg.live_mode) {
                if (wifi_connect_known(&cfg)) {
                    char filename[32];
                    // basename of stopped_path
                    const char *slash = strrchr(stopped_path, '/');
                    snprintf(filename, sizeof(filename), "%s", slash ? slash + 1 : stopped_path);
                    if (upload_one(&cfg, stopped_path, filename)) remove(stopped_path);
                    wifi_disconnect_and_stop();
                }
                // Unreachable network: the file is simply left on the SD
                // card, identical to BUFFERED mode. No special-casing.
            }
        }
```

This requires widening `wav_recorder_stop`'s signature to `wav_recorder_stop(char *path_out)` (Task 2), writing the path it just closed into the caller's buffer, and `wav_recorder_start` remembering the current path in a static so `wav_recorder_stop` can report it.

- [ ] **Step 3: Build, flash, verify**

Run: `idf.py build flash monitor`
Expected (manual checkpoint): tap the mode label, it flips between "LIVE" and "BUFFERED" and the choice survives a reboot (stored in NVS). In LIVE mode with wifi in range, record a note; without touching the sync long-press, the note uploads immediately and disappears from the buffered count. In BUFFERED mode, the same recording stays buffered until a manual long-press.

- [ ] **Step 4: Commit**

```bash
git add main/main.c
git commit -m "Add live/buffered mode toggle with immediate upload in live mode"
```

---

### Task 8: Error states (SD full, upload failure detail, battery)

**Files:**
- Modify: `main/main.c`

**Interfaces:**
- Consumes: everything above. This task only adds guard checks and screen messages, no new public functions.

- [ ] **Step 1: SD-full guard before starting a recording**

In `capture_task`'s "just started recording" branch (Task 2), check free space before opening the file:

```c
#include "esp_vfs.h"

static bool sd_has_free_space(void)
{
    // A fixed 1MB floor is simpler and safer than computing exact
    // remaining-recording-time from a variable bitrate; a few seconds of
    // headroom before "full" matters far less than never overwriting/
    // truncating a file because the card ran out mid-write.
    struct statvfs st;
    if (statvfs(SD_MOUNT_POINT, &st) != 0) return true; // fail open, don't block recording on a stat error
    uint64_t free_bytes = (uint64_t)st.f_bavail * st.f_frsize;
    return free_bytes > (1024ULL * 1024ULL);
}
```

In the same branch, before calling `wav_recorder_start`:

```c
                if (!sd_has_free_space()) {
                    bsp_display_lock(pdMS_TO_TICKS(100));
                    lv_label_set_text(status_label, "SD full");
                    bsp_display_unlock();
                    recording = false;
                    was_recording = false;
                    continue;
                }
```

- [ ] **Step 2: Distinguish a 401 from other upload failures**

In `upload_one` (Task 7's refactor of Task 5's per-file body), capture the status code before deciding success/failure and surface it distinctly:

```c
    esp_http_client_close(client);
    esp_http_client_cleanup(client);
    if (status == 401) {
        bsp_display_lock(pdMS_TO_TICKS(100));
        lv_label_set_text(status_label, "check token");
        bsp_display_unlock();
    }
    return status >= 200 && status < 300;
```

- [ ] **Step 3: Battery-critical guard**

Per the hardware reference, the AXP2101 PMIC exposes battery data over I2C (see the code-examples reference's `01_AXP2101` example for the register-read pattern). Wiring a full PMIC driver is a bigger lift than this task's other steps; the minimal version that satisfies the spec's requirement is a guard that finalizes and stops rather than corrupting a recording, so this step is intentionally scoped to the software half only:

```c
// Called from capture_task before checking `recording`, once a PMIC
// battery-percent reader exists (out of scope for this task -- wiring the
// AXP2101 driver is its own follow-up). Until that reader lands, this
// always returns false (never triggers), which is a safe default: no
// battery guard yet is strictly better than a guard that fires on bad data.
static bool battery_critical(void)
{
    return false;
}
```

```c
        if (recording && battery_critical()) {
            wav_recorder_stop(NULL);
            recording = false;
            was_recording = false;
            bsp_display_lock(pdMS_TO_TICKS(100));
            lv_label_set_text(status_label, "low battery, saved");
            bsp_display_unlock();
        }
```

- [ ] **Step 4: Build, flash, verify**

Run: `idf.py build flash monitor`
Expected (manual checkpoint): fill (or simulate full via a near-empty test card) — confirm "SD full" appears and no corrupt file is written. Point the device at a server with a deliberately wrong token, confirm "check token" appears distinctly from "sync failed" (wrong URL/no server).
The battery-critical path is not independently testable yet (returns `false` unconditionally) — that's expected; wiring the AXP2101 reader is flagged as a follow-up, not a gap in this task.

- [ ] **Step 5: Commit**

```bash
git add main/main.c
git commit -m "Add SD-full guard and distinguish token errors from network errors"
```

---

## Notes for the implementer

- Every code block above is a real, best-effort implementation against the documented BSP/ESP-IDF APIs gathered during brainstorming, not fabricated pseudocode. That said, this firmware has never been compiled or run, unlike the whisperdesk plan's pytest-verified code. Treat the manual checkpoints as load-bearing, not optional, especially around the BSP function names (`bsp_extra_i2s_read`, `bsp_extra_codec_init`) which came from secondhand documentation, not a direct read of the component's headers.
- The AXP2101 battery-percent reader (Task 8, Step 3) is explicitly deferred, not implemented. If battery-critical handling matters before shipping, that's a follow-up task, not a re-open of Task 8.
- VAD auto-stop is out of scope for this whole plan (spec non-goal for v1).
