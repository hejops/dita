// simple package for concurrent yt-dlp executions

package main

import (
	"bufio"
	"context"
	"database/sql"
	"fmt"
	"io"
	"log"
	"os"
	"os/user"
	"regexp"
	"strings"

	_ "github.com/mattn/go-sqlite3"
	"github.com/wader/goutubedl"
)

func array_split(slice []string, chunkSize int) [][]string {
	// https://freshman.tech/snippets/go/split-slice-into-chunks/
	var chunks [][]string
	// not incrementing will lead to truncation (e.g. 10/3 -> 3 3 3)
	chunkSize += 1
	for {
		if len(slice) == 0 {
			break
		}

		if len(slice) < chunkSize {
			chunkSize = len(slice)
		}

		chunks = append(chunks, slice[0:chunkSize])
		slice = slice[chunkSize:]
	}

	return chunks
}

func get_sub_urls(fname string) {
	// https://stackoverflow.com/a/16615559

	file, err := os.Open(fname)
	if err != nil {
		log.Fatal(err)
	}
	defer file.Close()

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		fmt.Println(scanner.Text())
	}

	if err := scanner.Err(); err != nil {
		log.Fatal(err)
	}
}

func download(url string, channel chan string) { // {{{
	// https://github.com/wader/goutubedl/blob/8b34407df2f32ea3710f99f404d2d1d6064bd82c/goutubedl_test.go#L442
	// https://github.com/wader/goutubedl/blob/8b34407df2f32ea3710f99f404d2d1d6064bd82c/goutubedl_test.go#L64
	// https://github.com/wader/goutubedl?tab=readme-ov-file#usage

	splits := strings.Split(url, "/")
	fname := strings.Split(splits[2], ".")[0] + "-" + splits[len(splits)-1] + ".mp3"
	fname = "./testdir/" + fname

	if _, err := os.Stat(fname); err == nil {
		// fmt.Println("already exists", fname)
		channel <- fname
		return
	}

	gdl, err := goutubedl.New(context.Background(), url, goutubedl.Options{})
	// gdl.Info
	if err != nil {
		// TODO: not received?
		fmt.Println("could not fetch", url)
		channel <- fname
		return
		// log.Fatal(err)
	}

	// result, err := result.Download(context.Background(), "best")
	fmt.Println("downloading", url)

	result, err := gdl.DownloadWithOptions(
		context.Background(),
		goutubedl.DownloadOptions{PlaylistIndex: 1}, // 1-indexed!
	)
	if err != nil {
		// log.Fatal(err)
		fmt.Println("could not download", url)
		channel <- fname
		return
	}
	defer result.Close()
	os.Mkdir("./testdir", 0750)

	file, err := os.Create(fname)
	if err != nil {
		log.Fatal(err)
	}
	defer file.Close()
	io.Copy(file, result)
	channel <- fname
} // }}}

func get_nb_urls() []string { // {{{
	// https://github.com/mattn/go-sqlite3/blob/master/_example/simple/simple.go

	usr, _ := user.Current()
	home := usr.HomeDir

	db, err := sql.Open("sqlite3", home+"/.config/newsboat/cache.db")
	if err != nil {
		log.Fatal(err)
	}
	defer db.Close()

	// [id guid title author url feedurl pubDate content unread enclosure_url enclosure_type enqueued flags deleted base content_mime_type enclosure_description enclosure_description_mime_type]
	rows, err := db.Query("SELECT content FROM rss_item;")
	if err != nil {
		log.Fatal(err)
	}

	// var urls map[string]string
	urls := make(map[string]int) // go has no hash set, but it does have hash map
	re, _ := regexp.Compile(`https://[^.]+\.bandcamp\.com/album/[-\w]+`)
	defer rows.Close()
	for rows.Next() {
		var content string
		err = rows.Scan(&content)
		if err != nil {
			log.Fatal(err)
		}
		matches := re.FindAllString(content, -1)
		if len(matches) > 0 {
			for _, match := range matches {
				urls[match] = 0
			}
		}
	}
	err = rows.Err()
	if err != nil {
		log.Fatal(err)
	}

	keys := make([]string, len(urls))

	i := 0
	for k := range urls {
		keys[i] = k
		i++
	}

	return keys
} // }}}

// https://stackoverflow.com/a/41439170
// https://koalatea.io/go-channels/
// channels -> single array directly https://stackoverflow.com/a/36563718

func main() {
	urls := get_nb_urls()
	n_chunks := 4
	chunks := array_split(urls, len(urls)/n_chunks)
	c := make(chan string)
	for i := 0; i < n_chunks; i++ {
		urls := chunks[i]
		go func() {
			for _, url := range urls {
				download(url, c)
			}
		}()
	}
	for i := 0; i < len(urls); i++ {
		<-c
	}
}
