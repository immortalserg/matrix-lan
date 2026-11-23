// Read-only deployment check. Credentials are read from stdin and are never
// printed, written to disk, or included in errors returned to the operator.
package main
import("crypto/aes";"crypto/cipher";"crypto/sha256";"crypto/tls";"encoding/base64";"encoding/json";"fmt";"net";"os";"strings";"time";"github.com/emersion/go-imap/v2";"github.com/emersion/go-imap/v2/imapclient";"golang.org/x/crypto/pbkdf2")
type account struct{Email,Username,Password string}
type input struct{Passphrase,Salt,Query string;Accounts []account}
func check(a account,key []byte,query string)map[string]any{
 out:=map[string]any{"email":a.Email}
 fail:=func(stage string)map[string]any{out["error_stage"]=stage;return out}
 raw,err:=base64.StdEncoding.DecodeString(strings.TrimPrefix(a.Password,"v2:"));if err!=nil||!strings.HasPrefix(a.Password,"v2:"){return fail("encrypted_credentials")}
 block,err:=aes.NewCipher(key);if err!=nil{return fail("key")};gcm,err:=cipher.NewGCM(block);if err!=nil||len(raw)<gcm.NonceSize(){return fail("ciphertext")}
 password,err:=gcm.Open(nil,raw[:gcm.NonceSize()],raw[gcm.NonceSize():],nil);if err!=nil{return fail("decrypt")}
 conn,err:=tls.DialWithDialer(&net.Dialer{Timeout:15*time.Second},"tcp","imap.gmail.com:993",&tls.Config{ServerName:"imap.gmail.com",MinVersion:tls.VersionTLS12});if err!=nil{return fail("tls")}
 defer conn.Close();conn.SetDeadline(time.Now().Add(90*time.Second));cli:=imapclient.New(conn,nil);defer cli.Close()
 if err=cli.Login(a.Username,string(password)).Wait();err!=nil{return fail("login")};clear(password)
 if _,err=cli.Select("INBOX",&imap.SelectOptions{ReadOnly:true}).Wait();err!=nil{return fail("readonly_inbox")}
 if !cli.Caps().Has(imap.Cap("X-GM-EXT-1")){return fail("gmail_capability")}
 queries:=map[string]string{"selected":query,"primary":"category:primary","updates":"category:updates","promotions":"category:promotions","social":"category:social","forums":"category:forums"}
 counts:=map[string]int{};sets:=map[string]map[imap.UID]bool{}
 for name,q:=range queries{
  criteria:=&imap.SearchCriteria{Since:time.Now().AddDate(0,0,-2),GmailRaw:[]string{q}}
  result,e:=cli.UIDSearch(criteria,nil).Wait();if e!=nil{return fail("search_"+name)}
  set:=map[imap.UID]bool{};for _,uid:=range result.AllUIDs(){set[uid]=true};sets[name]=set;counts[name]=len(set)
 }
 overlaps:=0;for _,name:=range []string{"promotions","social","forums"}{for uid:=range sets[name]{if sets["selected"][uid]{overlaps++}}}
 out["counts"]=counts;out["excluded_category_overlap"]=overlaps;out["ok"]=overlaps==0;return out
}
func main(){
 var in input;if json.NewDecoder(os.Stdin).Decode(&in)!=nil{fmt.Println(`{"error":"invalid private input"}`);os.Exit(1)}
 salt,err:=base64.StdEncoding.DecodeString(strings.TrimSpace(in.Salt));if err!=nil||len(salt)!=32{fmt.Println(`{"error":"salt"}`);os.Exit(1)}
 key:=pbkdf2.Key([]byte(strings.TrimSpace(in.Passphrase)),salt,100000,32,sha256.New);defer clear(key)
 results:=[]map[string]any{};for _,a:=range in.Accounts{results=append(results,check(a,key,in.Query))}
 json.NewEncoder(os.Stdout).Encode(map[string]any{"read_only":true,"query":in.Query,"accounts":results})
}
