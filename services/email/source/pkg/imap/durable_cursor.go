package imap

import (
 "crypto/sha256"
 "encoding/hex"
 "encoding/json"
 "errors"
 "fmt"
 "os"
 "path/filepath"
)

type mailCursor struct { Validity uint32 `json:"uid_validity"`; UID uint32 `json:"uid"` }

func (c *Client) cursorPath(mailbox string) string {
 dir:=os.Getenv("MATRIMAIL_CURSOR_DIR")
 if dir=="" || c.login==nil { return "" }
 key:=sha256.Sum256([]byte(c.login.UserMXID.String()+"\x00"+c.Email+"\x00"+mailbox))
 return filepath.Join(dir,hex.EncodeToString(key[:])+".json")
}

func initializeCursor(path string, validity, baseline uint32)(mailCursor,error){
 result:=mailCursor{Validity:validity,UID:baseline}
 if path=="" {return result,nil}
 raw,err:=os.ReadFile(path)
 if err==nil {
  var saved mailCursor
  if err=json.Unmarshal(raw,&saved);err!=nil{return result,fmt.Errorf("invalid saved mailbox cursor: %w",err)}
  if saved.Validity==validity {return saved,nil}
  // UIDVALIDITY changed: old numbers no longer identify the same messages.
  // Rescan from zero, letting Matrix message IDs deduplicate existing mail.
  result.UID=0
 } else if !errors.Is(err,os.ErrNotExist){return result,err}
 return result,saveCursor(path,result)
}

func saveCursor(path string, cursor mailCursor)error{
 if path=="" {return nil}
 if err:=os.MkdirAll(filepath.Dir(path),0700);err!=nil{return err}
 f,err:=os.CreateTemp(filepath.Dir(path),".cursor-*")
 if err!=nil{return err};name:=f.Name();defer os.Remove(name)
 if err=f.Chmod(0600);err==nil {err=json.NewEncoder(f).Encode(cursor)}
 if err==nil {err=f.Sync()}
 closeErr:=f.Close();if err!=nil{return err};if closeErr!=nil{return closeErr}
 return os.Rename(name,path)
}
