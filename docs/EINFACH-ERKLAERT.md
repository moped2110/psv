# psv – einfach erklärt

Diese Seite erklärt psv ohne Fachwissen. Wer daran entwickeln will, findet die
technische Anleitung in [DEVELOPER.md](DEVELOPER.md).

## Was ist psv?

psv ist ein Prüfwerkzeug für Bezahlsysteme, die mit **x402** arbeiten. Bei x402
bezahlt ein Programm, zum Beispiel ein KI-Agent, eine Web-Anfrage direkt mit
digitalem Geld. Bezahlt wird mit einem Stablecoin wie USDC auf einer Blockchain.

psv prüft **nicht**, ob eine Webseite das x402-Protokoll richtig spricht. Dafür
gibt es das Schwesterprojekt x402-conformance. psv prüft, ob das System
*hinter* der Webseite die Zahlungen richtig verbucht.

## Welches Problem löst es?

Ein Bezahlsystem führt Buch: „Bestellung 17 ist bezahlt", „Bestellung 18 ist
nicht bezahlt". Die Blockchain zeigt, ob das Geld wirklich angekommen ist. Diese
beiden Sichten können auseinanderlaufen. Dabei gibt es drei Arten von Fehlern:

- **Kunde hat bezahlt, bekommt aber nichts.** Das Geld ist angekommen, das
  System hält die Bestellung trotzdem für unbezahlt.
- **Kunde bekommt etwas geschenkt.** Das System hält die Bestellung für bezahlt,
  aber es ist kein Geld angekommen.
- **Kunde hat zu wenig bezahlt.** Das System bucht den vollen Betrag, angekommen
  ist aber weniger.

Solche Fehler fallen bei einer reinen Protokollprüfung nicht auf, denn die
Webseite antwortet ja formal korrekt. psv ist dafür gebaut, genau diese Fälle zu
finden.

## Wie funktioniert es?

So läuft eine Prüfung ab:

1. psv fragt das geprüfte System, was es über eine Bestellung glaubt: bezahlt
   oder nicht bezahlt.
2. psv liest **selbst** auf der Blockchain nach, was mit genau dieser Zahlung
   passiert ist. Es schaut sich eine bestimmte Transaktion an, die zugehörige
   Überweisungsmeldung des Tokens, Absender, Empfänger und Betrag. Die
   Kontostände liest es zu festgelegten Zeitpunkten (Blocknummern).
3. psv vergleicht beides und vergibt ein Urteil.

Dabei gelten zwei Grundsätze:

- **psv glaubt dem geprüften System nichts ungeprüft.** Was das System über sich
  selbst behauptet, ist nur eine Aussage, die psv kontrolliert. Für das Urteil
  zählt allein, was psv auf der Blockchain belegen kann.
- **Im Zweifel kein Urteil.** Sind die Daten unvollständig, widersprüchlich oder
  ist die Blockchain-Verbindung gestört, sagt psv das offen und vergibt kein
  „alles in Ordnung".

Ein Vergleich zur Ergänzung: psv arbeitet wie eine Buchprüferin. Sie nimmt das
Kassenbuch eines Ladens und hält es gegen die Kontoauszüge der Bank. Das Urteil
stützt sie auf die Kontoauszüge, nicht auf das Kassenbuch.

Für Tests bringt psv ein kleines Beispiel-Bezahlsystem mit, in das man gezielt
Fehler einbauen kann. Damit lässt sich zeigen, dass psv diese Fehler auch
wirklich erkennt. Solche Tests laufen auf einer lokalen Test-Blockchain (Anvil)
mit Spielgeld.

## Was bedeuten die Ergebnisse?

| Ergebnis | Bedeutung |
|---|---|
| **übereinstimmend bezahlt** (`consistent_paid`) | Das Geld ist angekommen, und das System weiß das. In Ordnung. |
| **übereinstimmend unbezahlt** (`consistent_unpaid`) | Es kam kein Geld, und das System liefert auch nichts aus. In Ordnung. |
| **stiller Verlust** (`silent_loss`) | Der Kunde hat bezahlt, das System glaubt es nicht. Kritischer Fehler. |
| **Phantom-Gutschrift** (`phantom_credit`) | Das System glaubt an eine Zahlung, die es nicht gab. Kritischer Fehler. |
| **zu wenig bezahlt** (`underpaid_credit`) | Das System bucht voll, angekommen ist weniger. Kritischer Fehler. |
| **mehr abgebucht als erlaubt** (`over_authorized_settlement`) | Bei Abrechnung nach Verbrauch: Vom Zahler ging mehr ab, als er höchstens freigegeben hatte. Kritischer Fehler. |

Das Kommandozeilenprogramm meldet dazu eine Zahl:

- `0` heißt übereinstimmend;
- `1` heißt kritischer Fehler gefunden;
- `2` heißt **kein Urteil möglich**, etwa wegen fehlerhafter Eingaben oder
  einer gestörten Verbindung.

**Ausstehend (PENDING):** Manchmal meldet ein Bezahlsystem: „Die Zahlung ist
abgeschickt, aber noch nicht bestätigt" (`settlement_pending`) und nennt dazu
die Transaktion. Seit Version 0.5.0 behandelt psv das als eigenen Zustand
**ausstehend**. Die Bestellung gilt dann weder als bezahlt noch als unbezahlt,
bis die genannte Transaktion auf der Blockchain nachgeprüft ist. Fehlt die
Transaktionsangabe, gibt es nichts nachzuprüfen. Dann zählt die Antwort als
nicht bezahlt.

Ein grünes Ergebnis gilt nur für die geprüften Fälle und Belege. Es ist kein
Gütesiegel für ein ganzes Bezahlsystem.

## Was psv nicht tut

- psv bewegt **kein echtes Geld**. Die Prüfbefehle lesen nur. Das
  Beispiel-Bezahlsystem darf ausschließlich auf lokalen Test-Blockchains oder
  ausdrücklich zugelassenen Testnetzen signieren, nie auf einem Hauptnetz.
- psv ist **keine Wallet**, verwahrt kein Geld und ist kein Zahlungsdienst.
- psv prüft **nicht das x402-Protokoll** selbst, also Format und Ablauf der
  Bezahl-Anfragen. Das macht x402-conformance.
- psv gibt **keine Rechts- oder Finanzberatung** und bescheinigt keine
  Rechtskonformität.
- psv bewertet nur die Blockchains und Token, die es kennt und überprüft hat.
  Unbekannte werden abgelehnt.

## Kleines Glossar

| Begriff | Erklärung |
|---|---|
| **x402** | Offenes Protokoll, mit dem ein Programm für eine einzelne Web-Anfrage bezahlt, ohne Konto und ohne Abo. |
| **HTTP 402** | Ein Statuscode des Webs: „Bezahlung erforderlich". Er war lange ungenutzt. x402 nutzt ihn für die Zahlungsaufforderung. |
| **Facilitator** | Dienst, der eine Zahlung im Auftrag des Anbieters prüft und auf der Blockchain ausführt. |
| **Settlement** | Die tatsächliche Ausführung der Zahlung auf der Blockchain, die „Abwicklung". |
| **Blockchain / Chain** | Öffentliches, fälschungssicheres Kassenbuch, in dem Überweisungen dauerhaft stehen. |
| **Transaktion, Transaktions-Hash** | Ein Eintrag auf der Blockchain und seine eindeutige Kennung, vergleichbar mit einer Belegnummer. |
| **Stablecoin, USDC** | Digitales Geld, dessen Wert an eine Währung gebunden ist (USDC an den US-Dollar). |
| **Testnet** | Eine Übungs-Blockchain mit Spielgeld ohne echten Wert. |
| **Mainnet** | Die echte Blockchain mit echtem Geld. psv bewegt dort nie Geld. |
| **Anvil** | Ein Programm, das eine private Test-Blockchain auf dem eigenen Rechner startet. |
| **RPC** | Die Schnittstelle, über die ein Programm Daten von einer Blockchain abfragt. |
| **SUT** | „System under test", das geprüfte Bezahlsystem. |
| **Rail** | Eine bestimmte Kombination aus Blockchain und Token, etwa USDC auf Base, die psv überprüft hat. |
| **Reorg** | Eine nachträgliche Umordnung der letzten Blöcke einer Blockchain. Eine scheinbar erfolgte Zahlung kann dadurch wieder verschwinden. |
