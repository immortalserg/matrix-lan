package imap

import (
 "fmt"
 "sort"
 "strings"
 "github.com/emersion/go-imap/v2"
 "github.com/emersion/go-imap/v2/imapclient"
)

// SetGmailInboxQuery is configured before the first connection. Filtering is
// intentionally limited to Gmail incoming mail; Sent and other providers keep
// their original processing, including attribution and delivery deduplication.
func (c *Client) SetGmailInboxQuery(query string) { c.gmailInboxQuery = strings.TrimSpace(query) }

func gmailFilterEnabled(host, query string, isSent bool) bool {
 host=strings.ToLower(strings.TrimSuffix(strings.TrimSpace(host),"."))
 return !isSent && strings.TrimSpace(query)!="" && (host=="imap.gmail.com" || host=="imap.googlemail.com")
}

func allowedGmailUIDs(uids []imap.UID, query string, search func(*imap.SearchCriteria)([]imap.UID,error)) (map[imap.UID]bool,error) {
 allowed:=make(map[imap.UID]bool)
 if len(uids)==0{return allowed,nil}
 set:=imap.UIDSetNum(uids...)
 matches,err:=search(&imap.SearchCriteria{UID:[]imap.UIDSet{set},GmailRaw:[]string{query}})
 if err!=nil{return nil,fmt.Errorf("Gmail category query failed; mailbox cursor retained: %w",err)}
 candidates:=make(map[imap.UID]bool,len(uids));for _,uid:=range uids{candidates[uid]=true}
 for _,uid:=range matches{if candidates[uid]{allowed[uid]=true}}
 return allowed,nil
}

func(c *Client) gmailAllowedUIDs(cli *imapclient.Client,uids []imap.UID,isSent bool)(map[imap.UID]bool,error){
 if !gmailFilterEnabled(c.Host,c.gmailInboxQuery,isSent){return nil,nil}
 if !cli.Caps().Has(imap.Cap("X-GM-EXT-1")){return nil,fmt.Errorf("Gmail category filter requires X-GM-EXT-1; cursor retained")}
 return allowedGmailUIDs(uids,c.gmailInboxQuery,func(criteria *imap.SearchCriteria)([]imap.UID,error){
  result,err:=cli.UIDSearch(criteria,nil).Wait();if err!=nil{return nil,err};return result.AllUIDs(),nil
 })
}

// Commit excluded UIDs as deliberate skips, while a failed allowed message
// retains its position. Skips never mark, move, delete or fetch the Gmail body.
func processUIDBatch(uids []imap.UID,allowed map[imap.UID]bool,process func(imap.UID)error,commit func(imap.UID)error)error {
 sort.Slice(uids,func(i,j int)bool{return uids[i]<uids[j]})
 for _,uid:=range uids{
  if allowed==nil||allowed[uid]{if err:=process(uid);err!=nil{return fmt.Errorf("process UID %d; cursor retained: %w",uid,err)}}
  if err:=commit(uid);err!=nil{return err}
 }
 return nil
}
