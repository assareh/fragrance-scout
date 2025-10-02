terraform {
  cloud {
    organization = "angryhippo"

    workspaces {
      name = "fragrance-scout"
    }
  }
}