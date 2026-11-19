#include <WiFi.h>
#include <HTTPClient.h>

const char* wifiSsid = "WIFI_NETWORK_NAME";
const char* wifiPassword = "WIFI_PASSWORD";
const char* scannerApiToken = "SCANNER_TOKEN";
const char* serverUrl = "http://<SERVER_IP>/api/tickets/scan/";

HardwareSerial ScannerSerial(2);
const int scannerRxPin = 16;
const int scannerTxPin = 17;
const unsigned long scanEndQuietMs = 300;
const int confirmationRetryCount = 10;
const unsigned long confirmationRetryDelayMs = 1000;
String scannerBuffer;
unsigned long lastScannerByteAt = 0;

String escapeJson(const String& value) {
  String escaped;
  escaped.reserve(value.length() + 8);

  for (size_t i = 0; i < value.length(); i++) {
    const char character = value[i];
    if (character == '"' || character == '\\') {
      escaped += '\\';
      escaped += character;
    } else if (character == '\n') {
      escaped += "\\n";
    } else if (character == '\r') {
      escaped += "\\r";
    } else if (character == '\t') {
      escaped += "\\t";
    } else if (static_cast<uint8_t>(character) >= 0x20) {
      escaped += character;
    }
  }

  return escaped;
}

long readJsonInteger(const String& json, const char* key) {
  const String keyMarker = String("\"") + key + "\"";
  const int keyPosition = json.indexOf(keyMarker);
  if (keyPosition < 0) {
    return -1;
  }

  int valuePosition = json.indexOf(':', keyPosition + keyMarker.length());
  if (valuePosition < 0) {
    return -1;
  }
  valuePosition++;

  while (
    valuePosition < json.length() &&
    (json[valuePosition] == ' ' || json[valuePosition] == '\t')
  ) {
    valuePosition++;
  }

  int valueEnd = valuePosition;
  while (
    valueEnd < json.length() &&
    json[valueEnd] >= '0' &&
    json[valueEnd] <= '9'
  ) {
    valueEnd++;
  }
  if (valueEnd == valuePosition) {
    return -1;
  }

  return json.substring(valuePosition, valueEnd).toInt();
}

bool connectToWiFi() {
  if (WiFi.status() == WL_CONNECTED) {
    return true;
  }

  Serial.printf("Connecting to Wi-Fi: %s\n", wifiSsid);
  WiFi.begin(wifiSsid, wifiPassword);

  const unsigned long startedAt = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - startedAt < 15000) {
    delay(250);
  }

  if (WiFi.status() != WL_CONNECTED) {
    Serial.println("Wi-Fi connection failed; will retry on the next scan.");
    return false;
  }

  Serial.print("Wi-Fi connected. ESP32 address: ");
  Serial.println(WiFi.localIP());
  return true;
}

void submitQrToken(const String& qrToken) {
  if (qrToken.length() == 0) {
    Serial.println("Empty QR data; scan ignored.");
    return;
  }
  if (qrToken.length() > 2048) {
    Serial.println("QR data is too long; scan ignored.");
    return;
  }
  if (String(scannerApiToken) == "SET_SCANNER_TOKEN") {
    Serial.println("Set scannerApiToken before scanning.");
    return;
  }
  if (!connectToWiFi()) {
    return;
  }

  const String payload = String("{\"qr_token\":\"") + escapeJson(qrToken) + "\"}";
  HTTPClient scanRequest;
  scanRequest.setTimeout(5000);
  if (!scanRequest.begin(serverUrl)) {
    Serial.println("Could not initialize the scan API request.");
    return;
  }
  scanRequest.addHeader("Content-Type", "application/json");
  scanRequest.addHeader("X-Scanner-Token", scannerApiToken);
  const int scanCode = scanRequest.POST(payload);
  const String scanBody = scanCode > 0 ? scanRequest.getString() : "";
  const String scanError = scanCode <= 0
    ? scanRequest.errorToString(scanCode)
    : "";
  scanRequest.end();

  if (scanCode == 200 && scanBody.indexOf("\"result\":\"accepted\"") >= 0) {
    Serial.println("ACCEPTED: ticket consumed on Solana.");
    return;
  }
  if (scanCode != 202 || scanBody.indexOf("\"result\":\"pending\"") < 0) {
    if (scanCode > 0) {
      Serial.printf("REJECTED: HTTP %d\n", scanCode);
      Serial.println(scanBody);
    } else {
      Serial.printf("Scan API request failed: %s\n", scanError.c_str());
    }
    return;
  }

  const long transactionId = readJsonInteger(scanBody, "chain_transaction_id");
  if (transactionId <= 0) {
    Serial.println("Scan is pending, but the response had no transaction ID.");
    Serial.println(scanBody);
    return;
  }

  Serial.println("Waiting for Solana confirmation...");
  const String statusUrl = String(serverUrl) + String(transactionId) + "/";
  for (int attempt = 0; attempt < confirmationRetryCount; attempt++) {
    delay(confirmationRetryDelayMs);

    HTTPClient statusRequest;
    statusRequest.setTimeout(5000);
    if (!statusRequest.begin(statusUrl)) {
      Serial.println("Could not initialize the scan status request.");
      return;
    }
    statusRequest.addHeader("X-Scanner-Token", scannerApiToken);
    const int statusCode = statusRequest.GET();
    const String statusBody = statusCode > 0 ? statusRequest.getString() : "";
    const String statusError = statusCode <= 0
      ? statusRequest.errorToString(statusCode)
      : "";

    if (
      statusCode == 200 &&
      statusBody.indexOf("\"result\":\"accepted\"") >= 0
    ) {
      Serial.println("ACCEPTED: ticket consumed on Solana.");
      statusRequest.end();
      return;
    }
    if (
      statusCode == 202 &&
      statusBody.indexOf("\"result\":\"pending\"") >= 0
    ) {
      statusRequest.end();
      continue;
    }

    if (statusCode > 0) {
      Serial.printf("REJECTED: HTTP %d\n", statusCode);
      Serial.println(statusBody);
    } else {
      Serial.printf("Scan status request failed: %s\n", statusError.c_str());
    }
    statusRequest.end();
    return;
  }

  Serial.println("STILL PENDING: do not admit until the scan is confirmed.");
}

void setup() {
  Serial.begin(115200);
  ScannerSerial.begin(9600, SERIAL_8N1, scannerRxPin, scannerTxPin);
  scannerBuffer.reserve(2048);

  WiFi.mode(WIFI_STA);
  connectToWiFi();
  Serial.printf(
    "QR ticket scanner ready. UART2: 9600 8N1, RX GPIO %d, TX GPIO %d\n",
    scannerRxPin,
    scannerTxPin
  );
}

void loop() {
  while (ScannerSerial.available()) {
    const char character = static_cast<char>(ScannerSerial.read());
    lastScannerByteAt = millis();

    if (character == '\r' || character == '\n') {
      if (scannerBuffer.length() > 0) {
        Serial.printf("Scanner sent %u bytes (line ending).\n", scannerBuffer.length());
        submitQrToken(scannerBuffer);
        scannerBuffer = "";
      }
      continue;
    }

    if (scannerBuffer.length() >= 2048) {
      Serial.println("Scanner input exceeded 2048 bytes; scan discarded.");
      scannerBuffer = "";
      continue;
    }

    scannerBuffer += character;
  }

  if (
    scannerBuffer.length() > 0 &&
    millis() - lastScannerByteAt >= scanEndQuietMs
  ) {
    Serial.printf("Scanner sent %u bytes (quiet timeout).\n", scannerBuffer.length());
    submitQrToken(scannerBuffer);
    scannerBuffer = "";
  }

  delay(5);
}
