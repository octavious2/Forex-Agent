//+------------------------------------------------------------------+
//|  LifeTap Forex Agent EA v2 — Limit Order Execution              |
//|  Places BUY LIMIT / SELL LIMIT at agent entry zone             |
//|  Auto-cancels after configurable expiry hours                  |
//+------------------------------------------------------------------+
#include <Trade\Trade.mqh>
#include <Trade\PositionInfo.mqh>
#include <Trade\OrderInfo.mqh>

CTrade        trade;
CPositionInfo posInfo;
COrderInfo    orderInfo;

string SIGNAL_FILE = "lifetap_signal.json";
string RESULT_FILE  = "lifetap_result.json";
string STATUS_FILE  = "lifetap_status.json";

int OnInit()
{
   trade.SetExpertMagicNumber(20260529);
   trade.SetDeviationInPoints(20);
   EventSetTimer(2);
   Print("LifeTap EA v2 started — LIMIT order mode");
   WriteStatus();
   return INIT_SUCCEEDED;
}

void OnTimer()
{
   WriteStatus();
   ProcessSignal();
   CheckExpiredOrders();
}

void ProcessSignal()
{
   int fh = FileOpen(SIGNAL_FILE, FILE_READ|FILE_TXT|FILE_ANSI);
   if(fh == INVALID_HANDLE) return;
   string content = "";
   while(!FileIsEnding(fh)) content += FileReadString(fh);
   FileClose(fh);
   if(content == "") return;

   bool executed = (StringFind(content,"\"executed\": true")>=0 ||
                    StringFind(content,"\"executed\":true")>=0);
   if(executed) return;

   string action     = ExtractString(content,"action");
   string symbol     = ExtractString(content,"symbol");
   string order_type = ExtractString(content,"order_type");
   int    sig_id     = (int)ExtractDouble(content,"id");
   double lot        = ExtractDouble(content,"lot");
   double limit_price= ExtractDouble(content,"limit_price");
   double entry_low  = ExtractDouble(content,"entry_low");
   double entry_high = ExtractDouble(content,"entry_high");
   double sl         = ExtractDouble(content,"sl");
   double tp1        = ExtractDouble(content,"tp1");
   double expiry_h   = ExtractDouble(content,"expiry_hours");

   if(action == "CLOSE")
   {
      int ticket = (int)ExtractDouble(content,"ticket");
      if(trade.PositionClose(ticket))
         WriteResult(sig_id,"closed",ticket,0,"Position closed");
      else
         WriteResult(sig_id,"error",0,0,"Close failed: "+IntegerToString(GetLastError()));
      MarkExecuted(); return;
   }

   if(action != "BUY" && action != "SELL") return;
   if(symbol == "" || lot <= 0 || limit_price <= 0) return;

   if(!SymbolSelect(symbol,true))
   {
      WriteResult(sig_id,"error",0,0,"Symbol not found: "+symbol);
      MarkExecuted(); return;
   }

   // Check if already have position or pending order on this symbol
   if(posInfo.Select(symbol))
   {
      WriteResult(sig_id,"skip",0,0,"Position already open on "+symbol);
      MarkExecuted(); return;
   }
   if(HasPendingOrder(symbol))
   {
      WriteResult(sig_id,"skip",0,0,"Pending order already exists for "+symbol);
      MarkExecuted(); return;
   }

   int    digits  = (int)SymbolInfoInteger(symbol,SYMBOL_DIGITS);
   double ask     = SymbolInfoDouble(symbol,SYMBOL_ASK);
   double bid     = SymbolInfoDouble(symbol,SYMBOL_BID);
   double spread  = ask - bid;

   // Normalise prices
   limit_price = NormalizeDouble(limit_price,digits);
   sl          = NormalizeDouble(sl,digits);
   tp1         = NormalizeDouble(tp1,digits);

   // Minimum stop distance
   double minStop = SymbolInfoInteger(symbol,SYMBOL_TRADE_STOPS_LEVEL)
                    * SymbolInfoDouble(symbol,SYMBOL_POINT);

   // Decide: LIMIT or MARKET based on current price vs entry zone
   bool useMarket = false;
   if(action == "BUY")
   {
      // If price is already at or below entry_high — go market
      if(ask <= entry_high * 1.0001) useMarket = true;
      // Adjust SL if too close
      if((limit_price - sl) < minStop) sl = limit_price - minStop*1.5;
   }
   else // SELL
   {
      // If price is already at or above entry_low — go market
      if(bid >= entry_low * 0.9999) useMarket = true;
      if((sl - limit_price) < minStop) sl = limit_price + minStop*1.5;
   }
   sl  = NormalizeDouble(sl,digits);

   // Set expiry datetime
   datetime expiry = TimeCurrent() + (int)(expiry_h * 3600);

   bool   ok     = false;
   ulong  ticket = 0;
   string type_label = "";

   if(useMarket)
   {
      // Price already in zone — market order
      if(action=="BUY")
         ok = trade.Buy(lot,symbol,ask,sl,tp1,"LifeTap #"+IntegerToString(sig_id));
      else
         ok = trade.Sell(lot,symbol,bid,sl,tp1,"LifeTap #"+IntegerToString(sig_id));
      ticket     = trade.ResultOrder();
      type_label = action+" MARKET";
   }
   else
   {
      // Place pending limit order
      if(action=="BUY")
         ok = trade.BuyLimit(lot,limit_price,symbol,sl,tp1,ORDER_TIME_SPECIFIED,expiry,
                             "LifeTap #"+IntegerToString(sig_id));
      else
         ok = trade.SellLimit(lot,limit_price,symbol,sl,tp1,ORDER_TIME_SPECIFIED,expiry,
                              "LifeTap #"+IntegerToString(sig_id));
      ticket     = trade.ResultOrder();
      type_label = action+" LIMIT @ "+DoubleToString(limit_price,digits);
   }

   if(ok && ticket > 0)
   {
      Print("LifeTap: Placed ",type_label," ",symbol,
            " lot=",lot," ticket=",ticket,
            " sl=",sl," tp=",tp1," expiry=",TimeToString(expiry));
      WriteResult(sig_id,"placed",(int)ticket,limit_price,
                  type_label+" "+symbol+" lot="+DoubleToString(lot,2));
   }
   else
   {
      int err = GetLastError();
      WriteResult(sig_id,"error",0,0,"Failed: "+IntegerToString(err)+
                  " action="+action+" price="+DoubleToString(limit_price,digits));
      Print("LifeTap ERROR ",err," action=",action," price=",limit_price);
   }

   MarkExecuted();
}

bool HasPendingOrder(string symbol)
{
   for(int i=0;i<OrdersTotal();i++)
   {
      if(orderInfo.SelectByIndex(i))
         if(orderInfo.Symbol()==symbol && orderInfo.Magic()==20260529)
            return true;
   }
   return false;
}

void CheckExpiredOrders()
{
   // Belt-and-suspenders: EA itself checks if any LifeTap orders are past expiry
   // MT5 handles ORDER_TIME_SPECIFIED expiry automatically — this is just a log
   static datetime lastCheck = 0;
   if(TimeCurrent() - lastCheck < 300) return;  // check every 5 min
   lastCheck = TimeCurrent();
   for(int i=OrdersTotal()-1;i>=0;i--)
   {
      if(orderInfo.SelectByIndex(i) && orderInfo.Magic()==20260529)
         Print("LifeTap pending: ",orderInfo.Symbol()," ",
               orderInfo.TypeDescription()," @ ",orderInfo.PriceOpen(),
               " expires ",TimeToString(orderInfo.TimeExpiration()));
   }
}

void WriteResult(int id,string status,int ticket,double price,string message)
{
   int fh=FileOpen(RESULT_FILE,FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(fh==INVALID_HANDLE) return;
   FileWriteString(fh,StringFormat(
      "{\"signal_id\":%d,\"status\":\"%s\",\"ticket\":%d,"
      "\"price\":%.5f,\"message\":\"%s\",\"time\":\"%s\"}",
      id,status,ticket,price,message,
      TimeToString(TimeGMT(),TIME_DATE|TIME_SECONDS)));
   FileClose(fh);
}

void WriteStatus()
{
   int fh=FileOpen(STATUS_FILE,FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(fh==INVALID_HANDLE) return;
   string positions="["; bool first=true;
   for(int i=0;i<PositionsTotal();i++)
   {
      if(posInfo.SelectByIndex(i))
      {
         if(!first) positions+=",";
         positions+=StringFormat(
            "{\"ticket\":%d,\"symbol\":\"%s\",\"type\":\"%s\","
            "\"lot\":%.2f,\"open\":%.5f,\"sl\":%.5f,\"profit\":%.2f}",
            (int)posInfo.Ticket(),posInfo.Symbol(),posInfo.TypeDescription(),
            posInfo.Volume(),posInfo.PriceOpen(),posInfo.StopLoss(),
            posInfo.Commission()+posInfo.Swap()+posInfo.Profit());
         first=false;
      }
   }
   positions+="]";
   string orders="["; first=true;
   for(int i=0;i<OrdersTotal();i++)
   {
      if(orderInfo.SelectByIndex(i) && orderInfo.Magic()==20260529)
      {
         if(!first) orders+=",";
         orders+=StringFormat(
            "{\"ticket\":%d,\"symbol\":\"%s\",\"type\":\"%s\","
            "\"lot\":%.2f,\"price\":%.5f,\"sl\":%.5f,\"expiry\":\"%s\"}",
            (int)orderInfo.Ticket(),orderInfo.Symbol(),
            orderInfo.TypeDescription(),orderInfo.VolumeCurrent(),
            orderInfo.PriceOpen(),orderInfo.StopLoss(),
            TimeToString(orderInfo.TimeExpiration()));
         first=false;
      }
   }
   orders+="]";

   // Build current spreads for the watched pairs (for scalping decisions)
   string spairs[] = {"EURUSD","GBPUSD","USDJPY","XAUUSD"};
   string spreads = "{";
   for(int i=0;i<ArraySize(spairs);i++)
   {
      double a  = SymbolInfoDouble(spairs[i],SYMBOL_ASK);
      double b  = SymbolInfoDouble(spairs[i],SYMBOL_BID);
      double pt = SymbolInfoDouble(spairs[i],SYMBOL_POINT);
      int dig   = (int)SymbolInfoInteger(spairs[i],SYMBOL_DIGITS);
      double pip = (dig==5 || dig==3) ? pt*10 : pt;
      double spread_pips = (pip>0) ? (a-b)/pip : 0;
      if(i>0) spreads += ",";
      spreads += StringFormat("\"%s\":{\"bid\":%.5f,\"ask\":%.5f,\"spread_pips\":%.2f}",
                              spairs[i], b, a, spread_pips);
   }
   spreads += "}";

   FileWriteString(fh,StringFormat(
      "{\"ea_active\":true,\"balance\":%.2f,\"equity\":%.2f,"
      "\"positions\":%s,\"pending_orders\":%s,\"spreads\":%s,\"time\":\"%s\"}",
      AccountInfoDouble(ACCOUNT_BALANCE),AccountInfoDouble(ACCOUNT_EQUITY),
      positions,orders,spreads,TimeToString(TimeGMT(),TIME_DATE|TIME_SECONDS)));
   FileClose(fh);
}

void MarkExecuted()
{
   int fh=FileOpen(SIGNAL_FILE,FILE_READ|FILE_TXT|FILE_ANSI);
   if(fh==INVALID_HANDLE) return;
   string content="";
   while(!FileIsEnding(fh)) content+=FileReadString(fh);
   FileClose(fh);
   StringReplace(content,"\"executed\": false","\"executed\": true");
   StringReplace(content,"\"executed\":false","\"executed\":true");
   fh=FileOpen(SIGNAL_FILE,FILE_WRITE|FILE_TXT|FILE_ANSI);
   if(fh==INVALID_HANDLE) return;
   FileWriteString(fh,content);
   FileClose(fh);
}

string ExtractString(string json,string key)
{
   string s="\""+key+"\": \""; int pos=StringFind(json,s);
   if(pos<0){s="\""+key+"\":\""; pos=StringFind(json,s);}
   if(pos<0) return "";
   int start=pos+StringLen(s);
   int end=StringFind(json,"\"",start);
   return StringSubstr(json,start,end-start);
}

double ExtractDouble(string json,string key)
{
   string s="\""+key+"\": "; int pos=StringFind(json,s);
   if(pos<0){s="\""+key+"\":"; pos=StringFind(json,s);}
   if(pos<0) return 0;
   return StringToDouble(StringSubstr(json,pos+StringLen(s),20));
}

void OnDeinit(const int reason){ EventKillTimer(); }
