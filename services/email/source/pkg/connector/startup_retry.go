package connector

import("context";"time")

// Retry a transport that failed before its watchdog could be registered.
// A successful start returns to the existing IDLE loop and watchdog.
func runConnectionStartup(ctx context.Context,attempt func()bool,delay time.Duration){
 for n:=0;;n++{
  if ctx.Err()!=nil{return}
  if attempt(){return}
  wait:=delay*time.Duration(1<<min(n,4));if wait>5*time.Minute{wait=5*time.Minute}
  timer:=time.NewTimer(wait)
  select{case<-ctx.Done():timer.Stop();return;case<-timer.C:}
 }
}
