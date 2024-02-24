// simple package for concurrent yt-dlp executions

package main

import (
	"bufio"
	"context"
	"fmt"
	"io"
	"log"
	"os"
	"os/user"
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

func read_lines() []string {
	usr, _ := user.Current()
	home := usr.HomeDir
	file, err := os.Open(home + "/.config/newsboat/cache.txt")
	if err != nil {
		log.Fatal(err)
	}
	defer file.Close()

	var urls []string

	scanner := bufio.NewScanner(file)
	for scanner.Scan() {
		fmt.Println(scanner.Text())
		urls = append(urls, scanner.Text())
	}

	if err := scanner.Err(); err != nil {
		log.Fatal(err)
	}
	return urls
}

func download(url string, channel chan string) { // {{{
	// https://github.com/wader/goutubedl/blob/8b34407df2f32ea3710f99f404d2d1d6064bd82c/goutubedl_test.go#L442
	// https://github.com/wader/goutubedl/blob/8b34407df2f32ea3710f99f404d2d1d6064bd82c/goutubedl_test.go#L64
	// https://github.com/wader/goutubedl?tab=readme-ov-file#usage

	splits := strings.Split(url, "/")
	fname := strings.Split(splits[2], ".")[0] + "-" + splits[len(splits)-1] + ".mp3"
	// TODO: cwd, not .
	fname = "./testdir/" + fname

	if _, err := os.Stat(fname); err == nil {
		// fmt.Println("already exists", fname)
		channel <- fname
		return
	}

	gdl, err := goutubedl.New(context.Background(), url, goutubedl.Options{})
	if err != nil {
		// TODO: handle 429
		fmt.Println("could not fetch", url)
		channel <- fname
		return
		// log.Fatal(err)
	}

	// result, err := result.Download(context.Background(), "best")
	fmt.Println("downloading", url)

	result, err := gdl.DownloadWithOptions(
		// TODO audio only
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

// https://stackoverflow.com/a/41439170
// https://koalatea.io/go-channels/
// channels -> single array directly https://stackoverflow.com/a/36563718

func main() {
	urls := read_lines()
	n_chunks := 3 // 4 chunks is very likely to 429
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
