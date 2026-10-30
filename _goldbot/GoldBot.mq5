//+------------------------------------------------------------------+
//| GoldBot.mq5 - XAUUSD trend-pullback bot for MetaTrader 5         |
//|                                                                  |
//| Trades with the H1 trend: when price pulls back to the 20-EMA on |
//| the entry timeframe and closes back in the trend's direction.    |
//| Every trade has a stop-loss and target, the stop moves to cost   |
//| at 1R, and hard limits stop the bot for the day.                 |
//+------------------------------------------------------------------+
#property copyright "Handral"
#property version   "1.00"
#property description "XAUUSD trend-pullback bot with hard risk limits."
#property description "Refuses to run on a real account unless AllowRealAccount is true."

#include <Trade/Trade.mqh>

input group "Safety"
input bool   AllowRealAccount    = false;  // Allow trading on a REAL account
input double RiskPercent         = 1.0;    // Risk per trade, % of balance
input double MaxDailyLossPercent = 3.0;    // Stop for the day after losing this % of balance
input int    MaxTradesPerDay     = 4;      // Most trades opened per day
input double MaxSpread           = 0.40;   // Skip if spread is wider than this ($ per oz)
input double MaxSlippage         = 0.50;   // Allowed slippage on entry ($ per oz)

input group "Strategy"
input ENUM_TIMEFRAMES EntryTF    = PERIOD_M15; // Entry timeframe
input ENUM_TIMEFRAMES TrendTF    = PERIOD_H1;  // Trend timeframe
input int    TrendFastEMA        = 50;     // Trend fast EMA
input int    TrendSlowEMA        = 200;    // Trend slow EMA
input int    EntryEMA            = 20;     // Pullback EMA on the entry timeframe
input int    RSIPeriod           = 14;     // RSI period (buy above 50, sell below 50)
input int    ATRPeriod           = 14;     // ATR period
input double SLBufferATR         = 0.2;    // Stop beyond the 3-candle swing by this x ATR
input double MinSL_ATR           = 0.5;    // Stop at least this x ATR away
input double MaxSL_ATR           = 2.5;    // Skip the trade if the stop is further than this x ATR
input double TargetR             = 2.0;    // Target, in multiples of the risk (R)
input double BreakevenAtR        = 1.0;    // Move stop to cost at this R (0 = off)

input group "Hours (broker server time; Exness = GMT)"
input int    StartHour           = 7;      // New trades from this hour
input int    EndHour             = 20;     // No new trades from this hour
input int    FridayLastEntryHour = 18;     // Friday: no new trades from this hour
input int    FridayCloseHour     = 20;     // Friday: close everything at this hour

input group "News"
input bool   AvoidNews           = true;   // No new trades near high-impact USD news
input int    NewsMinutesBefore   = 30;     // Minutes before the news
input int    NewsMinutesAfter    = 30;     // Minutes after the news

input group "Other"
input ulong  MagicNumber         = 240424; // Identifies this bot's trades
input bool   PushNotifications   = true;   // Send trades to the MT5 phone app

#define BTN_STOP  "GoldBot_btn_stop"
#define BTN_CLOSE "GoldBot_btn_close"

CTrade   trade;
int      hTrendFast = INVALID_HANDLE, hTrendSlow = INVALID_HANDLE;
int      hEntryEMA  = INVALID_HANDLE, hRSI = INVALID_HANDLE, hATR = INVALID_HANDLE;
datetime g_doneBar  = 0;      // entry-timeframe bar already evaluated
string   g_status   = "Starting";
string   g_lastSignal = "-";
datetime g_newsChecked = 0;
datetime g_newsTimes[];
bool     g_newsOk   = true;

bool IsTester() { return (bool)MQLInfoInteger(MQL_TESTER); }

string GVStop()     { return "GoldBot_stop_" + IntegerToString((long)MagicNumber); }
string GVLimitDay() { return "GoldBot_limitday_" + IntegerToString((long)MagicNumber); }

bool IsStopped() { return GlobalVariableCheck(GVStop()) && GlobalVariableGet(GVStop()) > 0; }
void SetStopped(bool on) { GlobalVariableSet(GVStop(), on ? 1 : 0); }

datetime DayStart()
{
   MqlDateTime t;
   TimeToStruct(TimeCurrent(), t);
   t.hour = 0; t.min = 0; t.sec = 0;
   return StructToTime(t);
}

double NormPrice(double p)
{
   double ts = SymbolInfoDouble(_Symbol, SYMBOL_TRADE_TICK_SIZE);
   if(ts <= 0) return NormalizeDouble(p, _Digits);
   return NormalizeDouble(MathRound(p / ts) * ts, _Digits);
}

void Notify(string msg)
{
   Print(msg);
   if(PushNotifications && !IsTester()) SendNotification("GoldBot " + _Symbol + ": " + msg);
}

void Journal(string event, string details)
{
   int h = FileOpen("GoldBot_journal.csv", FILE_READ | FILE_WRITE | FILE_CSV | FILE_ANSI | FILE_SHARE_READ, ',');
   if(h == INVALID_HANDLE) return;
   FileSeek(h, 0, SEEK_END);
   FileWrite(h, TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS), _Symbol, event, details);
   FileClose(h);
}

bool IsOurs()  // the currently selected position
{
   return PositionGetInteger(POSITION_MAGIC) == (long)MagicNumber && PositionGetString(POSITION_SYMBOL) == _Symbol;
}

int OurPositions()
{
   int n = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) > 0 && IsOurs()) n++;
   return n;
}

void CloseAll(string why)
{
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0 || !IsOurs()) continue;
      if(!trade.PositionClose(t))
         Print("Close failed for #", t, ": ", trade.ResultRetcodeDescription());
   }
   Journal("CLOSE ALL", why);
}

// Today's realised P/L (incl. costs), floating P/L, and trades opened, for this bot only.
void TodayStats(double &closed, double &floating, int &entries)
{
   closed = 0; floating = 0; entries = 0;
   if(HistorySelect(DayStart(), TimeCurrent() + 86400))
   {
      int n = HistoryDealsTotal();
      for(int i = 0; i < n; i++)
      {
         ulong d = HistoryDealGetTicket(i);
         if(d == 0) continue;
         if(HistoryDealGetInteger(d, DEAL_MAGIC) != (long)MagicNumber) continue;
         if(HistoryDealGetString(d, DEAL_SYMBOL) != _Symbol) continue;
         if(HistoryDealGetInteger(d, DEAL_ENTRY) == DEAL_ENTRY_IN) entries++;
         closed += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP)
                 + HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
      }
   }
   for(int i = PositionsTotal() - 1; i >= 0; i--)
      if(PositionGetTicket(i) > 0 && IsOurs())
         floating += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
}

// Move the stop to cost (plus a little) once the trade is BreakevenAtR in profit.
// The original risk is read back from the target: risk = |TP - open| / TargetR.
void ManagePositions()
{
   if(BreakevenAtR <= 0) return;
   double stopsMin = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   for(int i = PositionsTotal() - 1; i >= 0; i--)
   {
      ulong t = PositionGetTicket(i);
      if(t == 0 || !IsOurs()) continue;
      double open = PositionGetDouble(POSITION_PRICE_OPEN);
      double sl   = PositionGetDouble(POSITION_SL);
      double tp   = PositionGetDouble(POSITION_TP);
      if(tp <= 0) continue;
      double risk = MathAbs(tp - open) / TargetR;
      if(risk <= 0) continue;
      double lockIn = 0.05 * risk;
      if(PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY)
      {
         if(sl >= open || bid - open < BreakevenAtR * risk) continue;
         double nsl = NormPrice(open + lockIn);
         if(bid - nsl > stopsMin && trade.PositionModify(t, nsl, tp))
            Notify(StringFormat("BUY #%I64u at %sR: stop moved to cost %s", t, DoubleToString(BreakevenAtR, 1), DoubleToString(nsl, _Digits)));
      }
      else
      {
         if((sl > 0 && sl <= open) || open - ask < BreakevenAtR * risk) continue;
         double nsl = NormPrice(open - lockIn);
         if(nsl - ask > stopsMin && trade.PositionModify(t, nsl, tp))
            Notify(StringFormat("SELL #%I64u at %sR: stop moved to cost %s", t, DoubleToString(BreakevenAtR, 1), DoubleToString(nsl, _Digits)));
      }
   }
}

// High-impact USD events from MT5's built-in calendar (not available in the Strategy Tester).
bool NewsNear()
{
   if(!AvoidNews || IsTester()) return false;
   datetime now = TimeTradeServer();
   if(now - g_newsChecked > 900)
   {
      MqlCalendarValue v[];
      ArrayResize(g_newsTimes, 0);
      ResetLastError();
      int n = CalendarValueHistory(v, now - NewsMinutesAfter * 60 - 3600, now + 86400, NULL, "USD");
      g_newsOk = (n > 0 || GetLastError() == 0);
      for(int i = 0; i < n; i++)
      {
         MqlCalendarEvent ev;
         if(!CalendarEventById(v[i].event_id, ev) || ev.importance != CALENDAR_IMPORTANCE_HIGH) continue;
         int k = ArraySize(g_newsTimes);
         ArrayResize(g_newsTimes, k + 1);
         g_newsTimes[k] = v[i].time;
      }
      g_newsChecked = now;
   }
   for(int i = 0; i < ArraySize(g_newsTimes); i++)
      if(now >= g_newsTimes[i] - NewsMinutesBefore * 60 && now <= g_newsTimes[i] + NewsMinutesAfter * 60)
         return true;
   return false;
}

bool Copy1(int handle, int shift, double &val)
{
   double b[];
   if(CopyBuffer(handle, 0, shift, 1, b) != 1) return false;
   val = b[0];
   return true;
}

// +1 buy, -1 sell, 0 nothing. Uses only closed candles. Sets the stop-loss and ATR.
int GetSignal(double &sl, double &atr)
{
   double fast, slow, ema, rsi;
   if(!Copy1(hTrendFast, 1, fast) || !Copy1(hTrendSlow, 1, slow)) return 0;
   if(!Copy1(hEntryEMA, 1, ema) || !Copy1(hRSI, 1, rsi) || !Copy1(hATR, 1, atr)) return 0;
   double trendClose = iClose(_Symbol, TrendTF, 1);
   MqlRates r[];
   ArraySetAsSeries(r, true);
   if(CopyRates(_Symbol, EntryTF, 0, 5, r) < 5 || trendClose <= 0 || atr <= 0) return 0;

   bool up   = fast > slow && trendClose > slow;
   bool down = fast < slow && trendClose < slow;

   if(up && r[1].low <= ema && r[1].close > ema && r[1].close > r[1].open && rsi > 50)
   {
      sl = MathMin(r[1].low, MathMin(r[2].low, r[3].low)) - SLBufferATR * atr;
      return 1;
   }
   if(down && r[1].high >= ema && r[1].close < ema && r[1].close < r[1].open && rsi < 50)
   {
      sl = MathMax(r[1].high, MathMax(r[2].high, r[3].high)) + SLBufferATR * atr;
      return -1;
   }
   return 0;
}

// Lots that risk RiskPercent of balance between entry and stop; 0 (with a reason) if it can't be done.
double LotsForRisk(ENUM_ORDER_TYPE type, double entry, double sl, double maxRiskMoney, string &why)
{
   double balance = MathMin(AccountInfoDouble(ACCOUNT_BALANCE), AccountInfoDouble(ACCOUNT_EQUITY));
   double riskMoney = MathMin(balance * RiskPercent / 100.0, maxRiskMoney);
   double lossOneLot = 0;
   if(!OrderCalcProfit(type, _Symbol, 1.0, entry, sl, lossOneLot)) { why = "could not price the stop"; return 0; }
   lossOneLot = MathAbs(lossOneLot);
   if(lossOneLot <= 0) { why = "could not price the stop"; return 0; }

   double step = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_STEP);
   double minL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MIN);
   double maxL = SymbolInfoDouble(_Symbol, SYMBOL_VOLUME_MAX);
   double lots = MathFloor(riskMoney / lossOneLot / step + 1e-9) * step;
   if(lots < minL - 1e-9)
   {
      why = StringFormat("account too small: the minimum %s lot would risk %.1f%% (limit %.1f%%)",
                         DoubleToString(minL, 2), lossOneLot * minL / balance * 100.0, RiskPercent);
      return 0;
   }
   lots = MathMin(lots, maxL);
   double margin = 0;
   if(OrderCalcMargin(type, _Symbol, lots, entry, margin) && margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE) * 0.8)
   {
      why = "not enough free margin";
      return 0;
   }
   int digits = (int)MathMax(0, MathRound(-MathLog10(step)));
   return NormalizeDouble(lots, digits);
}

void TryEntry(double closedToday, double limitMoney)
{
   double sl, atr;
   int sig = GetSignal(sl, atr);
   if(sig == 0) { g_status = "Watching: no setup on the last candle"; return; }

   double ask = SymbolInfoDouble(_Symbol, SYMBOL_ASK);
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   double entry = sig > 0 ? ask : bid;
   double dist = MathAbs(entry - sl);
   if(dist > MaxSL_ATR * atr)
   {
      g_lastSignal = (sig > 0 ? "BUY" : "SELL") + " skipped: stop too far";
      g_status = "Watching";
      Print(g_lastSignal);
      return;
   }
   double stopsMin = SymbolInfoInteger(_Symbol, SYMBOL_TRADE_STOPS_LEVEL) * _Point + (ask - bid);
   dist = MathMax(dist, MathMax(MinSL_ATR * atr, stopsMin));
   sl = NormPrice(sig > 0 ? entry - dist : entry + dist);
   double tp = NormPrice(sig > 0 ? entry + TargetR * dist : entry - TargetR * dist);

   // Never let one trade take today's loss past the daily limit.
   double allowance = limitMoney + closedToday;
   string why = "";
   ENUM_ORDER_TYPE type = sig > 0 ? ORDER_TYPE_BUY : ORDER_TYPE_SELL;
   double lots = allowance > 0 ? LotsForRisk(type, entry, sl, allowance, why) : 0;
   if(allowance <= 0) why = "no daily loss allowance left";
   if(lots <= 0)
   {
      g_lastSignal = (sig > 0 ? "BUY" : "SELL") + " skipped: " + why;
      g_status = "Watching";
      Notify(g_lastSignal);
      Journal("SKIP", g_lastSignal);
      return;
   }

   string comment = "GoldBot";
   bool ok = sig > 0 ? trade.Buy(lots, _Symbol, 0, sl, tp, comment)
                     : trade.Sell(lots, _Symbol, 0, sl, tp, comment);
   uint rc = trade.ResultRetcode();
   if(ok && (rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_PLACED))
   {
      double price = trade.ResultPrice() > 0 ? trade.ResultPrice() : entry;
      g_lastSignal = StringFormat("%s %s lot at %s, SL %s, TP %s", sig > 0 ? "BUY" : "SELL",
                                  DoubleToString(lots, 2), DoubleToString(price, _Digits),
                                  DoubleToString(sl, _Digits), DoubleToString(tp, _Digits));
      Notify(g_lastSignal);
      Journal("OPEN", g_lastSignal);
      g_status = "In a trade";
   }
   else
   {
      g_lastSignal = (sig > 0 ? "BUY" : "SELL") + " failed: " + trade.ResultRetcodeDescription();
      Notify(g_lastSignal);
      Journal("ERROR", g_lastSignal);
   }
}

void MakeButton(string name, string text, int y, color bg)
{
   if(ObjectFind(0, name) < 0) ObjectCreate(0, name, OBJ_BUTTON, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, 10);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_XSIZE, 170);
   ObjectSetInteger(0, name, OBJPROP_YSIZE, 28);
   ObjectSetInteger(0, name, OBJPROP_BGCOLOR, bg);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clrWhite);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, 9);
   ObjectSetInteger(0, name, OBJPROP_STATE, false);
   ObjectSetString(0, name, OBJPROP_TEXT, text);
}

void DrawButtons()
{
   if(IsTester()) return;
   if(IsStopped()) MakeButton(BTN_STOP, "STOPPED - click to start", 200, clrDarkGreen);
   else            MakeButton(BTN_STOP, "STOP BOT", 200, clrFireBrick);
   MakeButton(BTN_CLOSE, "CLOSE ALL + STOP", 235, clrDimGray);
   ChartRedraw();
}

void UpdatePanel(double closed, double floating, int entries, double dayStartBal, bool limitHit)
{
   string acct = AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL ? "REAL" : "DEMO";
   double spread = SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);
   string s = "GoldBot  (" + acct + " account)\n";
   s += "Status: " + (IsStopped() ? "STOPPED by you" : limitHit ? "Daily loss limit hit - stopped until tomorrow" : g_status) + "\n";
   s += StringFormat("Today: %d/%d trades, P/L %.2f (closed %.2f, open %.2f)\n", entries, MaxTradesPerDay, closed + floating, closed, floating);
   s += StringFormat("Daily loss limit: %.2f (%.1f%% of %.2f)\n", dayStartBal * MaxDailyLossPercent / 100.0, MaxDailyLossPercent, dayStartBal);
   s += StringFormat("Spread: %.2f (max %.2f)\n", spread, MaxSpread);
   s += "Last signal: " + g_lastSignal + "\n";
   if(AvoidNews && !g_newsOk) s += "News calendar unavailable - news filter is off\n";
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED) || !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
      s += "!! Algo Trading is OFF - turn on the Algo Trading button\n";
   Comment(s);
}

int OnInit()
{
   if(!IsTester() && AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL && !AllowRealAccount)
   {
      Alert("GoldBot: this is a REAL account. Test on a demo account first; to trade real money set AllowRealAccount = true.");
      return INIT_FAILED;
   }
   if(StringFind(_Symbol, "XAU") < 0)
      Print("GoldBot: made for XAUUSD; attached to ", _Symbol);

   hTrendFast = iMA(_Symbol, TrendTF, TrendFastEMA, 0, MODE_EMA, PRICE_CLOSE);
   hTrendSlow = iMA(_Symbol, TrendTF, TrendSlowEMA, 0, MODE_EMA, PRICE_CLOSE);
   hEntryEMA  = iMA(_Symbol, EntryTF, EntryEMA, 0, MODE_EMA, PRICE_CLOSE);
   hRSI       = iRSI(_Symbol, EntryTF, RSIPeriod, PRICE_CLOSE);
   hATR       = iATR(_Symbol, EntryTF, ATRPeriod);
   if(hTrendFast == INVALID_HANDLE || hTrendSlow == INVALID_HANDLE || hEntryEMA == INVALID_HANDLE
      || hRSI == INVALID_HANDLE || hATR == INVALID_HANDLE)
   {
      Print("GoldBot: could not create indicators");
      return INIT_FAILED;
   }

   trade.SetExpertMagicNumber(MagicNumber);
   trade.SetDeviationInPoints((ulong)MathMax(1, MathRound(MaxSlippage / _Point)));
   trade.SetTypeFillingBySymbol(_Symbol);

   DrawButtons();
   Journal("START", AccountInfoInteger(ACCOUNT_TRADE_MODE) == ACCOUNT_TRADE_MODE_REAL ? "real account" : "demo account");
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   IndicatorRelease(hTrendFast); IndicatorRelease(hTrendSlow);
   IndicatorRelease(hEntryEMA);  IndicatorRelease(hRSI); IndicatorRelease(hATR);
   ObjectDelete(0, BTN_STOP);
   ObjectDelete(0, BTN_CLOSE);
   Comment("");
}

void OnTick()
{
   ManagePositions();

   MqlDateTime now;
   TimeToStruct(TimeCurrent(), now);
   if(now.day_of_week == 5 && now.hour >= FridayCloseHour && OurPositions() > 0)
   {
      CloseAll("Friday close");
      Notify("Friday close: all trades closed for the weekend");
   }

   double closed, floating;
   int entries;
   TodayStats(closed, floating, entries);
   double dayStartBal = AccountInfoDouble(ACCOUNT_BALANCE) - closed;
   double limitMoney = dayStartBal * MaxDailyLossPercent / 100.0;
   datetime today = DayStart();

   bool limitHit = GlobalVariableCheck(GVLimitDay()) && (datetime)GlobalVariableGet(GVLimitDay()) == today;
   if(!limitHit && closed + floating <= -limitMoney)
   {
      CloseAll("daily loss limit");
      GlobalVariableSet(GVLimitDay(), (double)today);
      limitHit = true;
      Notify(StringFormat("Daily loss limit hit (%.2f). Stopped until tomorrow.", closed + floating));
   }

   UpdatePanel(closed, floating, entries, dayStartBal, limitHit);

   // Entries: once per new candle on the entry timeframe.
   datetime bar = iTime(_Symbol, EntryTF, 0);
   if(bar == 0 || bar == g_doneBar) return;

   if(IsStopped() || limitHit) { g_doneBar = bar; return; }
   if(OurPositions() > 0)           { g_doneBar = bar; g_status = "In a trade"; return; }
   if(entries >= MaxTradesPerDay)   { g_doneBar = bar; g_status = "Max trades for today reached"; return; }
   bool friday = now.day_of_week == 5 && now.hour >= FridayLastEntryHour;
   if(now.hour < StartHour || now.hour >= EndHour || friday || now.day_of_week == 0 || now.day_of_week == 6)
   {
      g_doneBar = bar; g_status = "Outside trading hours"; return;
   }
   if(!MQLInfoInteger(MQL_TRADE_ALLOWED) || !TerminalInfoInteger(TERMINAL_TRADE_ALLOWED))
   {
      g_doneBar = bar; g_status = "Algo Trading is off"; return;
   }

   // Wide spread or news: keep retrying for the first 2 minutes of the candle.
   double spread = SymbolInfoDouble(_Symbol, SYMBOL_ASK) - SymbolInfoDouble(_Symbol, SYMBOL_BID);
   bool news = NewsNear();
   if(spread > MaxSpread || news)
   {
      g_status = news ? "Paused: high-impact USD news" : "Waiting: spread too wide";
      if(TimeCurrent() - bar >= 120) g_doneBar = bar;
      return;
   }

   g_doneBar = bar;
   TryEntry(closed, limitMoney);
}

void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
{
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD || !HistoryDealSelect(trans.deal)) return;
   if(HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != (long)MagicNumber) return;
   if(HistoryDealGetString(trans.deal, DEAL_SYMBOL) != _Symbol) return;
   long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY) return;

   double pl = HistoryDealGetDouble(trans.deal, DEAL_PROFIT) + HistoryDealGetDouble(trans.deal, DEAL_SWAP)
             + HistoryDealGetDouble(trans.deal, DEAL_COMMISSION) + HistoryDealGetDouble(trans.deal, DEAL_FEE);
   long reason = HistoryDealGetInteger(trans.deal, DEAL_REASON);
   string how = reason == DEAL_REASON_TP ? "target hit" : reason == DEAL_REASON_SL ? "stop hit"
              : reason == DEAL_REASON_SO ? "stop-out" : "closed";
   string msg = StringFormat("Trade %s at %s, P/L %.2f", how,
                             DoubleToString(HistoryDealGetDouble(trans.deal, DEAL_PRICE), _Digits), pl);
   Notify(msg);
   Journal("CLOSE", msg);
}

void OnChartEvent(const int id, const long &lparam, const double &dparam, const string &sparam)
{
   if(id != CHARTEVENT_OBJECT_CLICK) return;
   if(sparam == BTN_STOP)
   {
      SetStopped(!IsStopped());
      Notify(IsStopped() ? "Stopped by you: no new trades (open trades keep their stop and target)" : "Started by you");
      Journal(IsStopped() ? "STOP" : "START", "button");
   }
   else if(sparam == BTN_CLOSE)
   {
      if(MessageBox("Close all GoldBot trades and stop the bot?", "GoldBot", MB_YESNO | MB_ICONWARNING) == IDYES)
      {
         SetStopped(true);
         CloseAll("button");
         Notify("Closed all trades and stopped (button)");
      }
   }
   DrawButtons();
}
