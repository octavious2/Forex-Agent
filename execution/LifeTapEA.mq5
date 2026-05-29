//+------------------------------------------------------------------+
//|  LifeTap Forex Agent — Expert Advisor                            |
//|  Reads signals from lifetap_signal.json                         |
//|  Executes trades on demo account                                 |
//|  Writes results to lifetap_result.json                          |
//+------------------------------------------------------------------+
#property strict

#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>

CTrade        trade;
CPositionInfo posInfo;

string SIGNAL_FILE = "lifetap_signal.json";
string RESULT_FILE  = "lifetap_result.json";
string STATUS_FILE  = "lifetap_status.json";

datetime lastSignalTime = 0;

//+------------------------------------------------------------------+
int OnInit()
{
   trade.SetExpertMagicNumber(20260529);
   trade.SetDeviationInPoints(20);
   EventSetTimer(2);  // Check signal file every 2 seconds
   Print("LifeTap EA started — demo execution mode");
   WriteStatus();
   return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
void OnTimer()
{
   WriteStatus();      // Update status file every 2 seconds
   ProcessSignal();    // Check for new signal
}

//+------------------------------------------------------------------+
void ProcessSignal()
{
   // Read signal file
   int fh = FileOpen(SIGNAL_FILE, FILE_READ|FILE_TXT|FILE_ANSI);
   if(fh == INVALID_HANDLE) return;

   string content = "";
   while(!FileIsEnding(fh))
      content += FileReadString(fh);
   FileClose(fh);

   if(content == "") return;

   // Parse key fields from JSON manually (no JSON lib needed)
   string action  = ExtractString(content, "action");
   string symbol  = ExtractString(content, "symbol");
   string ts      = ExtractString(content, "timestamp");
   bool   executed = (StringFind(content, "\"executed\": true") >= 0 ||
                      StringFind(content, "\"executed\":true") >= 0);

   if(executed) return;  // Already processed this signal

   double lot    = ExtractDouble(content, "lot");
   double sl     = ExtractDouble(content, "sl");
   double tp1    = ExtractDouble(content, "tp1");
   double tp2    = ExtractDouble(content, "tp2");
   double tp3    = ExtractDouble(content, "tp3");
   int    sig_id = (int)ExtractDouble(content, "id");

   if(action == "CLOSE")
   {
      int ticket = (int)ExtractDouble(content, "ticket");
      if(trade.PositionClose(ticket))
         WriteResult(sig_id, "closed", ticket, 0, "Position closed");
      else
         WriteResult(sig_id, "error", 0, 0, "Close failed: " + IntegerToString(GetLastError()));
      MarkExecuted();
      return;
   }

   if(action != "BUY" && action != "SELL") return;
   if(symbol == "" || lot <= 0) return;

   // Validate symbol
   if(!SymbolSelect(symbol, true))
   {
      WriteResult(sig_id, "error", 0, 0, "Symbol not found: " + symbol);
      MarkExecuted();
      return;
   }

   // Check if we already have a position on this symbol
   if(posInfo.Select(symbol))
   {
      WriteResult(sig_id, "skip", 0, 0, "Position already open on " + symbol);
      MarkExecuted();
      return;
   }

   // Get current price
   double ask = SymbolInfoDouble(symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(symbol, SYMBOL_BID);
   double price = (action == "BUY") ? ask : bid;

   // Normalise SL and TP to symbol digits
   int digits = (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS);
   sl  = NormalizeDouble(sl,  digits);
   tp1 = NormalizeDouble(tp1, digits);

   // Minimum stop distance check
   double minStop = SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL)
                    * SymbolInfoDouble(symbol, SYMBOL_POINT);
   if(action == "BUY"  && (price - sl) < minStop) sl  = price - minStop * 1.5;
   if(action == "SELL" && (sl - price) < minStop) sl  = price + minStop * 1.5;
   sl = NormalizeDouble(sl, digits);

   // Execute trade
   bool ok = false;
   if(action == "BUY")
      ok = trade.Buy(lot, symbol, price, sl, tp1, "LifeTap #" + IntegerToString(sig_id));
   else
      ok = trade.Sell(lot, symbol, price, sl, tp1, "LifeTap #" + IntegerToString(sig_id));

   ulong ticket = trade.ResultOrder();

   if(ok && ticket > 0)
   {
      Print("LifeTap: Executed ", action, " ", symbol,
            " lot=", lot, " ticket=", ticket,
            " sl=", sl, " tp=", tp1);
      WriteResult(sig_id, "executed", (int)ticket, price,
                  action + " " + symbol + " lot=" + DoubleToString(lot,2));
   }
   else
   {
      int err = GetLastError();
      WriteResult(sig_id, "error", 0, 0,
                  "OrderSend failed. Error: " + IntegerToString(err));
      Print("LifeTap ERROR: ", err, " — ", action, " ", symbol);
   }

   MarkExecuted();
}

//+------------------------------------------------------------------+
void WriteResult(int id, string status, int ticket,
                 double price, string message)
{
   int fh = FileOpen(RESULT_FILE, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(fh == INVALID_HANDLE) return;
   string json = StringFormat(
      "{\"signal_id\":%d,\"status\":\"%s\",\"ticket\":%d,"
      "\"price\":%.5f,\"message\":\"%s\","
      "\"time\":\"%s\"}",
      id, status, ticket, price, message,
      TimeToString(TimeGMT(), TIME_DATE|TIME_SECONDS)
   );
   FileWriteString(fh, json);
   FileClose(fh);
}

//+------------------------------------------------------------------+
void WriteStatus()
{
   int fh = FileOpen(STATUS_FILE, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(fh == INVALID_HANDLE) return;

   string positions = "[";
   bool first = true;
   for(int i = 0; i < PositionsTotal(); i++)
   {
      if(posInfo.SelectByIndex(i))
      {
         if(!first) positions += ",";
         positions += StringFormat(
            "{\"ticket\":%d,\"symbol\":\"%s\",\"type\":\"%s\","
            "\"lot\":%.2f,\"open\":%.5f,\"sl\":%.5f,"
            "\"profit\":%.2f,\"magic\":%d}",
            (int)posInfo.Ticket(),
            posInfo.Symbol(),
            posInfo.TypeDescription(),
            posInfo.Volume(),
            posInfo.PriceOpen(),
            posInfo.StopLoss(),
            posInfo.Commission() + posInfo.Swap() + posInfo.Profit(),
            (int)posInfo.Magic()
         );
         first = false;
      }
   }
   positions += "]";

   string json = StringFormat(
      "{\"ea_active\":true,\"balance\":%.2f,"
      "\"equity\":%.2f,\"positions\":%s,"
      "\"time\":\"%s\"}",
      AccountInfoDouble(ACCOUNT_BALANCE),
      AccountInfoDouble(ACCOUNT_EQUITY),
      positions,
      TimeToString(TimeGMT(), TIME_DATE|TIME_SECONDS)
   );
   FileWriteString(fh, json);
   FileClose(fh);
}

//+------------------------------------------------------------------+
void MarkExecuted()
{
   // Overwrite signal file marking it as executed
   int fh = FileOpen(SIGNAL_FILE, FILE_READ|FILE_TXT|FILE_ANSI);
   if(fh == INVALID_HANDLE) return;
   string content = "";
   while(!FileIsEnding(fh)) content += FileReadString(fh);
   FileClose(fh);
   StringReplace(content, "\"executed\": false", "\"executed\": true");
   StringReplace(content, "\"executed\":false",  "\"executed\":true");
   fh = FileOpen(SIGNAL_FILE, FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(fh == INVALID_HANDLE) return;
   FileWriteString(fh, content);
   FileClose(fh);
}

//+------------------------------------------------------------------+
// Simple JSON string extractor
string ExtractString(string json, string key)
{
   string search = "\"" + key + "\": \"";
   int pos = StringFind(json, search);
   if(pos < 0) {
      search = "\"" + key + "\":\"";
      pos = StringFind(json, search);
   }
   if(pos < 0) return "";
   int start = pos + StringLen(search);
   int end   = StringFind(json, "\"", start);
   if(end < 0) return "";
   return StringSubstr(json, start, end - start);
}

double ExtractDouble(string json, string key)
{
   string search = "\"" + key + "\": ";
   int pos = StringFind(json, search);
   if(pos < 0) {
      search = "\"" + key + "\":";
      pos = StringFind(json, search);
   }
   if(pos < 0) return 0;
   int start = pos + StringLen(search);
   string val = StringSubstr(json, start, 20);
   return StringToDouble(val);
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   Print("LifeTap EA stopped");
}
