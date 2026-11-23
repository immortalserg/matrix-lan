package connector

import("fmt";"net/mail";"strings";"unicode")

func validateGmailCredentials(address,password string)(string,string,error){
 address=strings.ToLower(strings.TrimSpace(address))
 parsed,err:=mail.ParseAddress(address)
 if err!=nil || parsed.Address!=address{return "","",fmt.Errorf("Укажите полный адрес Gmail, например name@gmail.com")}
 parts:=strings.Split(address,"@")
 if len(parts)!=2 || (parts[1]!="gmail.com"&&parts[1]!="googlemail.com"){
  return "","",fmt.Errorf("Эта форма предназначена для адресов @gmail.com и @googlemail.com")
 }
 password=strings.Map(func(r rune)rune{if unicode.IsSpace(r){return -1};return r},password)
 if len(password)!=16{return "","",fmt.Errorf("Нужен отдельный пароль приложения Google: 16 букв. Создайте его в разделе Google «Безопасность → Пароли приложений»")}
 for _,r:=range password{if r<'a'||r>'z'{return "","",fmt.Errorf("Пароль приложения Google содержит 16 маленьких латинских букв")}}
 return address,password,nil
}
