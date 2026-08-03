package handler

import (
	"database/sql"
	"encoding/json"
	"net/http"
)

func GetUser(db *sql.DB, w http.ResponseWriter, r *http.Request) {
	id := r.URL.Query().Get("id")
	var name string
	var email string
	db.QueryRow("SELECT name, email FROM users WHERE id = $1", id).Scan(&name, &email)
	resp := map[string]string{"name": name, "email": email}
	json.NewEncoder(w).Encode(resp)
}
