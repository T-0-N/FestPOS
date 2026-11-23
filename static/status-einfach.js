/* FestPOS – einfache Statusanzeige (/status-einfach, app/status_simple.py) für alte Browser wie Safari 9 (iOS 9.3.5).
   Holt alle data-sec Sekunden nur den Inhalt (/status-einfach?teil=1) und tauscht ihn in #s aus – die Seite wird
   nicht neu geladen und blitzt deshalb nicht weiss auf.
   Bewusst nur ES5: kein let/const, keine Pfeilfunktionen, kein fetch, keine Promises, keine Template-Strings.
   - Zwei Fehlschläge nacheinander: body bekommt die Klasse «off» (Hinweis oben, Inhalt abgedunkelt). Es wird immer
     weiter versucht; sobald der Server wieder antwortet, verschwindet der Hinweis.
   - Andere Version (Update) oder 401 (Anmeldung abgelaufen): die ganze Seite neu laden – Safari fragt dann nach
     Benutzer und Passwort bzw. holt das neue CSS und Skript. */
(function () {
  "use strict";
  var box = document.getElementById("s");
  if (!box || !window.XMLHttpRequest) {
    return;
  }
  var sec = parseInt(box.getAttribute("data-sec"), 10) || 10;
  var version = box.getAttribute("data-v") || "";
  var fails = 0;
  var TIMEOUT = 8000;                                   // ms bis ein Abruf als Fehlschlag gilt

  function setOff(off) {
    var cls = (" " + document.body.className + " ").replace(/ off /g, " ").replace(/^\s+|\s+$/g, "");
    document.body.className = off ? (cls ? cls + " off" : "off") : cls;
  }

  function reload() {
    window.location.reload();
  }

  function schedule() {
    window.setTimeout(load, sec * 1000);
  }

  function load() {
    var xhr = new XMLHttpRequest();
    var done = false;
    var timer;

    function finish(status, text, ver) {
      if (done) {
        return;
      }
      done = true;
      window.clearTimeout(timer);
      if (status === 401 || (status === 200 && ver && version && ver !== version)) {
        reload();
        return;
      }
      if (status === 200) {
        fails = 0;
        box.innerHTML = text;
        setOff(false);
      } else {
        fails += 1;
        if (fails >= 2) {
          setOff(true);
        }
      }
      schedule();
    }

    timer = window.setTimeout(function () {
      finish(0, "", "");
      try {
        xhr.abort();
      } catch (e) {
        /* schon beendet */
      }
    }, TIMEOUT);
    xhr.onreadystatechange = function () {
      if (xhr.readyState === 4) {
        finish(xhr.status, xhr.responseText, xhr.getResponseHeader("X-FestPOS-Version"));
      }
    };
    try {
      xhr.open("GET", "/status-einfach?teil=1", true);
      xhr.send();
    } catch (e) {
      finish(0, "", "");
    }
  }

  schedule();
}());
