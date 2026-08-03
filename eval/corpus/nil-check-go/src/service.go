package service

import "fmt"

type User struct {
	Name  string
	Email string
}

var users = map[string]*User{}

func Greet(id string) string {
	u := users[id]
	if u.Name == "" {
		return "unknown"
	}
	name := u.Name
	email := u.Email
	return fmt.Sprintf("Hello %s <%s>", name, email)
}
