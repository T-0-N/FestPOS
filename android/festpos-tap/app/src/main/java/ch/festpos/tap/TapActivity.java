package ch.festpos.tap;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.Intent;
import android.net.Uri;
import android.os.Bundle;
import android.widget.TextView;

import org.json.JSONException;
import org.json.JSONObject;

import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;

/**
 * FestPOS Tap (T13): Begleit-App für die Kartenzahlung über Payrexx mit NFC am XCover.
 *
 * PAYREXX-UNGETESTET: noch nie gegen ein echtes Payrexx-Konto gelaufen (docs/testplan-payrexx.md).
 *
 * Die Bestell-App (Fully) startet diese Activity mit
 *   intent://pay#Intent;scheme=festpostap;package=ch.festpos.tap;S.id=…;S.token=…;S.amount=12.50;S.ref=…;S.server=…;end
 * Sie ruft die Payrexx-App über ihre App-zu-App-Schnittstelle auf (Intent com.payrexx.taptopay.SOFTPOS, siehe
 * github.com/payrexx/TapToPaySDK), der Gast hält Karte oder Handy an das Gerät, und das Ergebnis geht mit dem
 * Einmal-Schlüssel an den Pi (POST {server}/api/tap/result). Danach schliesst sie sich – zurück in der Bestell-App.
 * Die App speichert nichts und kennt keine Kartendaten (die bleiben in der zertifizierten Payrexx-App).
 */
public class TapActivity extends Activity {
    private static final int REQUEST = 4711;
    private static final String PAYREXX_ACTION = "com.payrexx.taptopay.SOFTPOS";
    private static final String PAYREXX_CONFIG = "com.payrexx.taptopay.CONFIGURATION";
    private static final String PAYREXX_RESULT = "com.payrexx.taptopay.RESULT";

    private String server, id, token, amount, ref, method;
    private boolean show;
    private TextView text;
    private boolean reported;

    @Override
    protected void onCreate(Bundle state) {
        super.onCreate(state);
        text = new TextView(this);
        text.setPadding(48, 48, 48, 48);
        text.setTextSize(20);
        text.setText(R.string.wait);
        setContentView(text);
        read(getIntent());
        if (state != null) {
            return;                                      // neu aufgebaut (z.B. gedreht): Ergebnis kommt noch
        }
        if (!valid()) {
            finish();
            return;
        }
        try {
            Intent pay = new Intent(PAYREXX_ACTION);
            pay.putExtra(PAYREXX_CONFIG, request().toString());
            startActivityForResult(pay, REQUEST);
        } catch (ActivityNotFoundException e) {
            text.setText(R.string.no_payrexx);
            report(false, null, "no_payrexx_app");
        } catch (JSONException e) {
            report(false, null, "request");
        }
    }

    /** Angaben aus dem Intent (S.…-Extras von intent://) oder als Fallback aus der Adresse (festpostap://pay?…). */
    private void read(Intent in) {
        Uri u = in.getData();
        server = pick(in, u, "server");
        id = pick(in, u, "id");
        token = pick(in, u, "token");
        amount = pick(in, u, "amount");
        ref = pick(in, u, "ref");
        method = pick(in, u, "method");
        show = "1".equals(pick(in, u, "show"));
    }

    private static String pick(Intent in, Uri u, String key) {
        String v = in.getStringExtra(key);
        if (v == null && u != null && u.isHierarchical()) {
            v = u.getQueryParameter(key);
        }
        return v == null ? "" : v;
    }

    /** Nur https zu einer Adresse im lokalen Netz (Pi), Betrag und Schlüssel vorhanden. */
    private boolean valid() {
        try {
            URL u = new URL(server);
            String h = u.getHost();
            boolean local = h.startsWith("192.168.") || h.startsWith("10.") || h.matches("172\\.(1[6-9]|2\\d|3[01])\\..*")
                    || h.equals("festpos.local");
            Double.parseDouble(amount);
            return "https".equals(u.getProtocol()) && local && id.matches("\\d+") && token.length() >= 10;
        } catch (Exception e) {
            return false;
        }
    }

    /** Anfrage wie Payrexx TapToPaySDK (Sale): {"payload": "{\"data\":{…},\"operation\":\"sale\"}", "signature": ""}. */
    private JSONObject request() throws JSONException {
        JSONObject data = new JSONObject();
        data.put("order_reference", ref);
        data.put("show_result", show);
        data.put("amount", amount);
        data.put("tip", "0.0");
        data.put("extras", "");
        data.put("payment_method_type", method.isEmpty() ? JSONObject.NULL : method);
        JSONObject payload = new JSONObject();
        payload.put("data", data);
        payload.put("operation", "sale");
        JSONObject req = new JSONObject();
        req.put("payload", payload.toString());
        req.put("signature", "");
        return req;
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        super.onActivityResult(requestCode, resultCode, data);
        if (requestCode != REQUEST) {
            return;
        }
        String result = null;
        if (resultCode == RESULT_OK && data != null && data.getExtras() != null) {
            result = data.getExtras().getString(PAYREXX_RESULT);
        }
        text.setText(R.string.report);
        report(result != null, result, result == null ? "cancelled" : null);
    }

    /** Ergebnis an den Pi (bis zu 3 Versuche), dann zurück zur Bestell-App. */
    private void report(boolean ok, String result, String message) {
        if (reported) {
            return;
        }
        reported = true;
        new Thread(() -> {
            try {
                JSONObject body = new JSONObject();
                body.put("id", Long.parseLong(id));
                body.put("token", token);
                body.put("ok", ok);
                body.put("result", result == null ? JSONObject.NULL : result);
                body.put("message", message == null ? JSONObject.NULL : message);
                byte[] bytes = body.toString().getBytes(StandardCharsets.UTF_8);
                for (int i = 0; i < 3; i++) {
                    try {
                        HttpURLConnection c = (HttpURLConnection) new URL(server + "/api/tap/result").openConnection();
                        c.setConnectTimeout(8000);
                        c.setReadTimeout(8000);
                        c.setRequestMethod("POST");
                        c.setDoOutput(true);
                        c.setRequestProperty("Content-Type", "application/json");
                        try (OutputStream o = c.getOutputStream()) {
                            o.write(bytes);
                        }
                        int code = c.getResponseCode();
                        c.disconnect();
                        if (code < 500) {
                            break;                       // angenommen (oder abgewiesen: nicht wiederholen)
                        }
                    } catch (Exception e) {
                        Thread.sleep(1500L * (i + 1));
                    }
                }
            } catch (Exception e) {
                // Ohne Verbindung bleibt die Zahlung in FestPOS «offen»; die Bedienung bestätigt von Hand.
            }
            runOnUiThread(this::finish);
        }).start();
    }
}
